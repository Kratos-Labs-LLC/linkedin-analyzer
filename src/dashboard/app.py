"""Flask control panel for the LinkedIn Post Analyzer.

Routes:
  /                -> dashboard home (run status, readiness, recent activity)
  /creators        -> creator CRUD (writes back to creators.yaml)
  /posts           -> filterable post browser
  /posts/<id>      -> single post detail
  /runs            -> run history
  /analysis        -> day-31 readiness + launch
  /skill           -> view generated SKILL.md
  /actions         -> trigger / cancel / view-log for current job
  /logs/<name>     -> raw log tail

All writes are local. Bind only to 127.0.0.1 — no auth.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, url_for

from src import storage
from src.config import DEFAULT_CREATORS_PATH, AppConfig, load_config
from src.dashboard import actions, creators_yaml

log = logging.getLogger(__name__)

MIN_POSTS = 800
MIN_CREATORS_WITH_20 = 15
MIN_DAYS_ELAPSED = 30


def _readiness(cfg: AppConfig) -> list[dict]:
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        s = storage.collection_summary(conn)

    days = None
    if s["first_collected_at"]:
        try:
            first = datetime.fromisoformat(s["first_collected_at"].replace("Z", "+00:00"))
            if first.tzinfo is None:
                first = first.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - first).days
        except ValueError:
            days = None

    return [
        {
            "label": f"≥{MIN_DAYS_ELAPSED} days of collection",
            "actual": f"{days if days is not None else 0} days",
            "ok": bool(days is not None and days >= MIN_DAYS_ELAPSED),
        },
        {
            "label": f"≥{MIN_POSTS} posts",
            "actual": f"{s['total_posts']} posts",
            "ok": s["total_posts"] >= MIN_POSTS,
        },
        {
            "label": f"≥{MIN_CREATORS_WITH_20} creators with ≥20 posts",
            "actual": f"{s['creators_with_20_posts']} creators",
            "ok": s["creators_with_20_posts"] >= MIN_CREATORS_WITH_20,
        },
    ]


def create_app(cfg: AppConfig | None = None) -> Flask:
    cfg = cfg or load_config()
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    app.secret_key = "linkedin-analyzer-dashboard"  # local only; not a secret
    app.config["APP_CFG"] = cfg

    @app.context_processor
    def inject_nav():
        return {"nav_items": [
            ("Dashboard", "index"),
            ("Creators", "creators_page"),
            ("Posts", "posts_page"),
            ("Runs", "runs_page"),
            ("Analysis", "analysis_page"),
            ("Skill", "skill_page"),
            ("Actions", "actions_page"),
        ]}

    @app.route("/")
    def index():
        storage.init_db(cfg.db_path)
        with storage.connect(cfg.db_path) as conn:
            recent = [dict(r) for r in storage.recent_runs(conn, limit=10)]
            summary = storage.collection_summary(conn)
            n_creators = conn.execute(
                "SELECT COUNT(*) AS n FROM creators WHERE active = 1"
            ).fetchone()["n"]
            avg_score = conn.execute(
                "SELECT AVG(engagement_score) AS s FROM posts WHERE engagement_score IS NOT NULL"
            ).fetchone()["s"]

        job = actions.refresh_state(cfg.logs_dir)
        authed = (cfg.chrome_profile_dir / ".authed").exists()

        return render_template(
            "index.html",
            recent_runs=recent,
            summary=summary,
            n_creators=n_creators,
            avg_score=avg_score,
            readiness=_readiness(cfg),
            current_job=job,
            job_running=actions.is_running(cfg.logs_dir),
            authed=authed,
        )

    # --- Creators ----------------------------------------------------------

    @app.route("/creators")
    def creators_page():
        storage.init_db(cfg.db_path)
        with storage.connect(cfg.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM creators ORDER BY active DESC, weight DESC, display_name ASC"
            )]
            post_counts = {r["creator_id"]: r["n"] for r in conn.execute(
                "SELECT creator_id, COUNT(*) AS n FROM posts GROUP BY creator_id"
            )}
        for r in rows:
            r["post_count"] = post_counts.get(r["id"], 0)
        yaml_path = DEFAULT_CREATORS_PATH
        return render_template("creators.html", creators=rows, yaml_path=yaml_path)

    @app.route("/creators/add", methods=["POST"])
    def creators_add():
        url = request.form.get("url", "").strip()
        name = request.form.get("name", "").strip()
        anchor = request.form.get("anchor") == "on"
        if not url or not name:
            flash("URL and name are required.", "error")
            return redirect(url_for("creators_page"))
        try:
            creators_yaml.add_creator(DEFAULT_CREATORS_PATH, url=url, name=name, anchor=anchor)
        except Exception as e:
            flash(f"Failed to add: {e}", "error")
            return redirect(url_for("creators_page"))
        _resync_creators(cfg)
        flash(f"Added {name}.", "ok")
        return redirect(url_for("creators_page"))

    @app.route("/creators/remove", methods=["POST"])
    def creators_remove():
        url = request.form.get("url", "").strip()
        if not url:
            abort(400)
        if creators_yaml.remove_creator(DEFAULT_CREATORS_PATH, url):
            _resync_creators(cfg)
            flash(f"Removed {url}.", "ok")
        else:
            flash(f"URL not found in creators.yaml: {url}", "error")
        return redirect(url_for("creators_page"))

    @app.route("/creators/toggle_anchor", methods=["POST"])
    def creators_toggle_anchor():
        url = request.form.get("url", "").strip()
        anchor = request.form.get("anchor") == "1"
        if creators_yaml.set_anchor(DEFAULT_CREATORS_PATH, url, anchor):
            _resync_creators(cfg)
            flash(
                f"Moved to {'anchors' if anchor else 'standard'}.",
                "ok",
            )
        else:
            flash("Creator not found.", "error")
        return redirect(url_for("creators_page"))

    # --- Posts -------------------------------------------------------------

    @app.route("/posts")
    def posts_page():
        storage.init_db(cfg.db_path)
        creator_id = request.args.get("creator_id", type=int)
        min_score = request.args.get("min_score", type=float)
        sort = request.args.get("sort", "engagement")
        limit = min(request.args.get("limit", default=100, type=int), 500)

        sql = (
            "SELECT p.*, c.display_name AS creator_name "
            "FROM posts p JOIN creators c ON c.id = p.creator_id WHERE 1=1"
        )
        params: list = []
        if creator_id:
            sql += " AND p.creator_id = ?"
            params.append(creator_id)
        if min_score is not None:
            sql += " AND p.engagement_score >= ?"
            params.append(min_score)
        if sort == "date":
            sql += " ORDER BY p.collected_at DESC"
        else:
            sql += " ORDER BY p.engagement_score DESC NULLS LAST"
        sql += " LIMIT ?"
        params.append(limit)

        with storage.connect(cfg.db_path) as conn:
            rows = [dict(r) for r in conn.execute(sql, tuple(params))]
            creators = [dict(r) for r in conn.execute(
                "SELECT id, display_name FROM creators WHERE active = 1 ORDER BY display_name"
            )]

        for r in rows:
            text = r.get("post_text") or ""
            r["preview"] = text[:240] + ("…" if len(text) > 240 else "")

        return render_template(
            "posts.html",
            posts=rows,
            creators=creators,
            filters={"creator_id": creator_id, "min_score": min_score, "sort": sort, "limit": limit},
        )

    @app.route("/posts/<int:post_id>")
    def post_detail(post_id: int):
        with storage.connect(cfg.db_path) as conn:
            row = conn.execute(
                "SELECT p.*, c.display_name AS creator_name, c.linkedin_url AS creator_url "
                "FROM posts p JOIN creators c ON c.id = p.creator_id WHERE p.id = ?",
                (post_id,),
            ).fetchone()
            if row is None:
                abort(404)
            features_row = conn.execute(
                "SELECT features_json FROM post_features WHERE post_id = ?",
                (post_id,),
            ).fetchone()
        features = None
        if features_row:
            try:
                features = json.loads(features_row["features_json"])
            except json.JSONDecodeError:
                features = None
        return render_template("post_detail.html", post=dict(row), features=features)

    # --- Runs --------------------------------------------------------------

    @app.route("/runs")
    def runs_page():
        storage.init_db(cfg.db_path)
        with storage.connect(cfg.db_path) as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 100"
            )]
        for r in rows:
            r["errors"] = json.loads(r["errors_json"]) if r["errors_json"] else None
        return render_template("runs.html", runs=rows)

    # --- Analysis ----------------------------------------------------------

    @app.route("/analysis")
    def analysis_page():
        readiness = _readiness(cfg)
        ready = all(g["ok"] for g in readiness)
        stats_path = cfg.output_dir / "stats.json"
        skill_path = cfg.output_dir / "linkedin-high-engagement-writer" / "SKILL.md"
        return render_template(
            "analysis.html",
            readiness=readiness,
            ready=ready,
            stats_exists=stats_path.exists(),
            skill_exists=skill_path.exists(),
            job_running=actions.is_running(cfg.logs_dir),
        )

    @app.route("/analysis/run", methods=["POST"])
    def analysis_run():
        skill_path = request.form.get("leadmagnet_skill_path", "").strip()
        force = request.form.get("force") == "on"
        skip_extract = request.form.get("skip_extract") == "on"
        skip_synth = request.form.get("skip_synth") == "on"
        if not skill_path:
            flash("leadmagnet-post-writer SKILL.md path is required.", "error")
            return redirect(url_for("analysis_page"))
        args = ["--leadmagnet-skill-path", skill_path]
        if force:
            args.append("--force")
        if skip_extract:
            args.append("--skip-extract")
        if skip_synth:
            args.append("--skip-synth")
        try:
            actions.launch("analysis", _repo_root(), cfg.logs_dir, extra_args=args)
            flash("Analysis started. Follow progress on the Actions page.", "ok")
        except RuntimeError as e:
            flash(str(e), "error")
        return redirect(url_for("actions_page"))

    @app.route("/skill")
    def skill_page():
        skill_path = cfg.output_dir / "linkedin-high-engagement-writer" / "SKILL.md"
        stats_path = cfg.output_dir / "stats.json"
        top_path = cfg.output_dir / "top_posts.md"
        bottom_path = cfg.output_dir / "bottom_posts.md"
        skill_md = skill_path.read_text() if skill_path.exists() else None
        stats_json = stats_path.read_text() if stats_path.exists() else None
        top_md = top_path.read_text() if top_path.exists() else None
        bottom_md = bottom_path.read_text() if bottom_path.exists() else None
        return render_template(
            "skill.html",
            skill_md=skill_md,
            stats_json=stats_json,
            top_md=top_md,
            bottom_md=bottom_md,
            skill_path=skill_path,
        )

    # --- Actions -----------------------------------------------------------

    @app.route("/actions")
    def actions_page():
        state = actions.refresh_state(cfg.logs_dir)
        log_tail = ""
        if state:
            log_tail = actions.tail_log(Path(state.log_path))
        return render_template(
            "actions.html",
            state=state,
            running=actions.is_running(cfg.logs_dir),
            log_tail=log_tail,
        )

    @app.route("/actions/launch", methods=["POST"])
    def actions_launch():
        kind = request.form.get("kind")
        if kind not in actions.JOB_TYPES:
            flash(f"Unknown job kind: {kind}", "error")
            return redirect(url_for("actions_page"))
        try:
            actions.launch(kind, _repo_root(), cfg.logs_dir)
            flash(f"Started {kind} job.", "ok")
        except RuntimeError as e:
            flash(str(e), "error")
        return redirect(url_for("actions_page"))

    @app.route("/actions/cancel", methods=["POST"])
    def actions_cancel():
        if actions.cancel(cfg.logs_dir):
            flash("Cancel signal sent.", "ok")
        else:
            flash("No running job to cancel.", "error")
        return redirect(url_for("actions_page"))

    @app.route("/actions/clear", methods=["POST"])
    def actions_clear():
        actions.clear_state(cfg.logs_dir)
        flash("Cleared job state.", "ok")
        return redirect(url_for("actions_page"))

    # --- Filters / helpers -------------------------------------------------

    @app.template_filter("fmt_score")
    def fmt_score(val):
        if val is None:
            return "—"
        return f"{val:.4f}"

    @app.template_filter("fmt_ts")
    def fmt_ts(val):
        if not val:
            return "—"
        return val.split(".")[0].replace("T", " ")

    return app


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resync_creators(cfg: AppConfig) -> None:
    """After editing creators.yaml, reload + sync into the DB."""
    from src.config import load_creators

    new_creators = load_creators(DEFAULT_CREATORS_PATH)
    cfg.creators = new_creators
    storage.init_db(cfg.db_path)
    with storage.connect(cfg.db_path) as conn:
        storage.sync_creators(conn, new_creators)

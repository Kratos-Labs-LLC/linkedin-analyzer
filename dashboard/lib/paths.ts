import path from 'node:path';

export const REPO_ROOT = path.resolve(process.cwd(), '..');

export const DB_PATH = path.join(REPO_ROOT, 'db', 'analyzer.db');
export const CREATORS_YAML_PATH = path.join(REPO_ROOT, 'creators.yaml');
export const CHROME_PROFILE_DIR = path.join(REPO_ROOT, 'chrome_profile');
export const LOGS_DIR = path.join(REPO_ROOT, 'logs');
export const OUTPUT_DIR = path.join(REPO_ROOT, 'output');
export const VENV_PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python');
export const AUTH_SENTINEL = path.join(CHROME_PROFILE_DIR, '.authed');

export const ANCHOR_WEIGHT = 1.5;
export const STANDARD_WEIGHT = 1.0;

// Path to the existing leadmagnet-post-writer SKILL.md, used by the dashboard
// draft panel for side-by-side comparisons. Read at request time so the user
// can move it without restarting the server (if they reload the page after).
export function leadmagnetSkillPath(): string | null {
  return process.env.LEADMAGNET_SKILL_PATH || null;
}

export const NEW_SKILL_PATH = path.join(
  OUTPUT_DIR,
  'linkedin-high-engagement-writer',
  'SKILL.md',
);

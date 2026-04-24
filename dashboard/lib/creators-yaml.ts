import fs from 'node:fs';
import yaml from 'js-yaml';

import { ANCHOR_WEIGHT, STANDARD_WEIGHT, CREATORS_YAML_PATH } from './paths';

export type CreatorEntry = { url: string; name: string };

export type CreatorsData = {
  anchors: CreatorEntry[];
  standard: CreatorEntry[];
};

const HEADER = `# Curated LinkedIn creators. Managed via dashboard or hand-edited.
# anchors -> weight ${ANCHOR_WEIGHT}; standard -> weight ${STANDARD_WEIGHT}.

`;

export function readYaml(p = CREATORS_YAML_PATH): CreatorsData {
  if (!fs.existsSync(p)) return { anchors: [], standard: [] };
  const raw = fs.readFileSync(p, 'utf8');
  const parsed = (yaml.load(raw) as Partial<CreatorsData>) || {};
  return {
    anchors: (parsed.anchors ?? []).map(normalizeEntry),
    standard: (parsed.standard ?? []).map(normalizeEntry),
  };
}

function normalizeEntry(e: CreatorEntry): CreatorEntry {
  return { url: String(e.url), name: String(e.name ?? '') };
}

export function writeYaml(data: CreatorsData, p = CREATORS_YAML_PATH) {
  const body = yaml.dump(
    { anchors: data.anchors, standard: data.standard },
    { noRefs: true, lineWidth: 120, quotingType: '"' },
  );
  fs.writeFileSync(p, HEADER + body);
}

export function addCreator(
  args: { url: string; name: string; anchor: boolean },
  p = CREATORS_YAML_PATH,
) {
  const data = readYaml(p);
  const entry: CreatorEntry = { url: args.url, name: args.name };
  const bucket = args.anchor ? 'anchors' : 'standard';
  const other = args.anchor ? 'standard' : 'anchors';
  data[other] = data[other].filter((e) => e.url !== args.url);
  data[bucket] = [...data[bucket].filter((e) => e.url !== args.url), entry];
  writeYaml(data, p);
}

export function removeCreator(url: string, p = CREATORS_YAML_PATH): boolean {
  const data = readYaml(p);
  const before = data.anchors.length + data.standard.length;
  data.anchors = data.anchors.filter((e) => e.url !== url);
  data.standard = data.standard.filter((e) => e.url !== url);
  const after = data.anchors.length + data.standard.length;
  if (after < before) {
    writeYaml(data, p);
    return true;
  }
  return false;
}

export function setAnchor(url: string, anchor: boolean, p = CREATORS_YAML_PATH): boolean {
  const data = readYaml(p);
  const src = anchor ? 'standard' : 'anchors';
  const dst = anchor ? 'anchors' : 'standard';
  const moved = data[src].filter((e) => e.url === url);
  if (moved.length === 0) return false;
  data[src] = data[src].filter((e) => e.url !== url);
  data[dst] = [...data[dst].filter((e) => e.url !== url), ...moved];
  writeYaml(data, p);
  return true;
}

export function flatCreatorsFromYaml(p = CREATORS_YAML_PATH) {
  const data = readYaml(p);
  return [
    ...data.anchors.map((e) => ({ ...e, weight: ANCHOR_WEIGHT })),
    ...data.standard.map((e) => ({ ...e, weight: STANDARD_WEIGHT })),
  ];
}

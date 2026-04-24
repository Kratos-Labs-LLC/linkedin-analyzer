'use server';

import { revalidatePath } from 'next/cache';
import { redirect } from 'next/navigation';

import {
  addCreator as yamlAdd,
  flatCreatorsFromYaml,
  removeCreator as yamlRemove,
  setAnchor as yamlSetAnchor,
} from './creators-yaml';
import { syncCreatorsFromYaml } from './db';
import { cancel, clearState, JOB_KINDS, launch, type JobKind } from './jobs';
import { sendTestAlert } from './telegram';

function resyncCreators() {
  syncCreatorsFromYaml(flatCreatorsFromYaml());
}

export async function addCreatorAction(formData: FormData) {
  const url = String(formData.get('url') ?? '').trim();
  const name = String(formData.get('name') ?? '').trim();
  const anchor = formData.get('anchor') === 'on';
  if (!url || !name) {
    return { ok: false, message: 'URL and name are required.' };
  }
  yamlAdd({ url, name, anchor });
  resyncCreators();
  revalidatePath('/creators');
  revalidatePath('/');
  return { ok: true, message: `Added ${name}.` };
}

export async function removeCreatorAction(formData: FormData) {
  const url = String(formData.get('url') ?? '').trim();
  if (!url) return { ok: false, message: 'URL missing.' };
  const removed = yamlRemove(url);
  if (!removed) return { ok: false, message: `Not found: ${url}` };
  resyncCreators();
  revalidatePath('/creators');
  revalidatePath('/');
  return { ok: true, message: `Removed ${url}.` };
}

export async function toggleAnchorAction(formData: FormData) {
  const url = String(formData.get('url') ?? '').trim();
  const anchor = formData.get('anchor') === '1';
  const ok = yamlSetAnchor(url, anchor);
  if (!ok) return { ok: false, message: 'Creator not found.' };
  resyncCreators();
  revalidatePath('/creators');
  return {
    ok: true,
    message: `Moved to ${anchor ? 'anchors' : 'standard'}.`,
  };
}

export async function launchJobAction(formData: FormData) {
  const raw = String(formData.get('kind') ?? '');
  if (!(JOB_KINDS as readonly string[]).includes(raw)) {
    return { ok: false, message: `Unknown job kind: ${raw || '(empty)'}` };
  }
  const kind = raw as JobKind;
  try {
    launch(kind);
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : String(e) };
  }
  revalidatePath('/actions');
  revalidatePath('/');
  return { ok: true, message: `Started ${kind}.` };
}

export async function cancelJobAction() {
  const ok = cancel();
  revalidatePath('/actions');
  return ok
    ? { ok: true, message: 'Cancel signal sent.' }
    : { ok: false, message: 'No running job to cancel.' };
}

export async function clearJobStateAction() {
  clearState();
  revalidatePath('/actions');
  return { ok: true, message: 'Cleared job state.' };
}

export async function sendTestAlertAction() {
  const r = await sendTestAlert();
  revalidatePath('/actions');
  return { ok: r.ok, message: `Telegram test alert: ${r.detail}` };
}

export async function runAnalysisAction(formData: FormData): Promise<void> {
  const skillPath = String(formData.get('leadmagnet_skill_path') ?? '').trim();
  const force = formData.get('force') === 'on';
  const skipExtract = formData.get('skip_extract') === 'on';
  const skipSynth = formData.get('skip_synth') === 'on';
  if (!skillPath) {
    redirect(
      '/analysis?error=' + encodeURIComponent('Path to leadmagnet-post-writer/SKILL.md is required.'),
    );
  }
  const extra = ['--leadmagnet-skill-path', skillPath];
  if (force) extra.push('--force');
  if (skipExtract) extra.push('--skip-extract');
  if (skipSynth) extra.push('--skip-synth');
  try {
    launch('analysis', extra);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    redirect('/analysis?error=' + encodeURIComponent(msg));
  }
  revalidatePath('/actions');
  redirect('/actions');
}

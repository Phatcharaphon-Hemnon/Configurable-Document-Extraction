import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

async function load(entryPoints) {
  const result = await build({ entryPoints, bundle: true, platform: 'node', format: 'esm', write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}

const { getPipelineStage, getFailedStageLabel, getResultKind } = await load(['src/utils/pipeline.ts']);

const ocrFail = { failed_stage: 'ocr', error: 'OCR text incoherent: the page could not be transcribed reliably', fields: [], validation_errors: [], judge: null };
const extractorFail = { failed_stage: 'extractor', error: 'Extractor failed: LLM output failed (table_root)', fields: [], validation_errors: [], judge: null };
const partial = { failed_stage: null, error: null, fields: [{ name: 'a', value: '1' }], validation_errors: ['x'], needs_review: true, acceptance_status: 'unresolved', judge: null, completeness_score: 0.5 };
const completed = { failed_stage: null, error: null, fields: [{ name: 'a', value: '1' }], validation_errors: [], needs_review: false, acceptance_status: 'accepted', judge: { score: 0.9, issues: [] }, completeness_score: 1 };

test('OCR failure does not leave Router active; OCR is the responsible stage', () => {
  assert.equal(getPipelineStage(ocrFail), -1);
  assert.equal(getFailedStageLabel(ocrFail), 'OCR');
  assert.equal(getResultKind(ocrFail), 'unreadable_source');
});

test('Extractor failure reports its own stage, not Router', () => {
  assert.equal(getPipelineStage(extractorFail), 1);
  assert.equal(getFailedStageLabel(extractorFail), 'Extractor');
  assert.equal(getResultKind(extractorFail), 'technical_failure');
});

test('partial vs completed are distinguished', () => {
  assert.equal(getResultKind(partial), 'partial');
  assert.equal(getResultKind(completed), 'completed');
});

test('skipped/unavailable Judge is never passed (kind stays partial, not completed)', () => {
  const skipped = { ...completed, judge: null, judge_status: 'skipped', needs_review: true, acceptance_status: 'accepted' };
  assert.equal(getResultKind(skipped), 'partial');
});

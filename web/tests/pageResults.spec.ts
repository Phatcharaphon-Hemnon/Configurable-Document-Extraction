import { expect, test } from '@playwright/test';

const labels = ['ลำดับ', 'รหัสสินค้า', 'รายการสินค้า', 'จำนวน', 'ราคาต่อหน่วย', 'รวม', 'ส่วนลด%', 'จำนวนเงิน'];
const documents = [1, 2].map((page) => ({
  id: `page-${page}`, doc_type: page === 1 ? 'invoice' : 'purchase_order', language: page === 1 ? 'th' : 'en',
  fields: [], tables: page === 1 ? [{ name: 'line_items', columns: labels.map((label, i) => ({ key: `c${i}`, label })),
    rows: [[1, '5415280238060', 'ORELIE BP Plover Black', 1, 5490, 5490, 20, 4392].map((value, i) => ({column: `c${i}`, value, confidence: .9, source_span: String(value)}))],
  }] : [], validation_errors: [], needs_review: false, completeness_score: 1, judge_status: 'skipped',
  full_text: page === 1 ? 'ใบกำกับภาษี' : 'Purchase order',
  source: { source_id: 'source', filename: 'mixed.pdf', page_number: page, page_count: 2,
    preview_url: `/sources/source/pages/${page}`, download_url: '/sources/source' },
}));
const result = { request: {filename: 'mixed.pdf'}, documents };

test('mixed pages retain Thai columns and page sources; opening history never submits extraction', async ({ page }) => {
  let submissions = 0;
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (route.request().method() === 'POST') submissions++;
    if (path === '/api/') body = { model: 'test' };
    else if (path === '/api/extract') body = { job_id: 'job', status: 'queued' };
    else if (path === '/api/jobs/job') body = { job_id: 'job', status: 'completed', result };
    else if (path === '/api/history/stats') body = { total_jobs: 1, avg_completeness: 1, needs_review: 0, by_status: {completed: 1} };
    else if (path === '/api/history') body = { total: 1, jobs: [{ id:'job', filename:'mixed.pdf', status:'completed', doc_type:'mixed', language:'mixed', completeness_score:1, created_at:'2026-09-09T00:00:00Z' }] };
    else if (path === '/api/history/job') body = { result };
    else if (path.startsWith('/api/sources/')) return route.fulfill({contentType:'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB1sAAAAASUVORK5CYII=', 'base64')});
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await page.locator('input[type=file]').setInputFiles({name:'mixed.pdf', mimeType:'application/pdf', buffer:Buffer.from('%PDF-fake')});
  await expect(page.getByRole('columnheader', {name:'ส่วนลด%', exact:true})).toBeVisible();
  for (const label of labels) await expect(page.getByRole('columnheader', {name:label, exact:true})).toBeVisible();
  await expect(page.getByAltText('Source page 1')).toHaveAttribute('src', /\/pages\/1$/);
  await page.locator('.page-tabs button').nth(1).click();
  await expect(page.getByAltText('Source page 2')).toHaveAttribute('src', /\/pages\/2$/);
  expect(submissions).toBe(1);
  await page.getByRole('button', {name:'History', exact:true}).click();
  await page.getByRole('button', {name:'mixed.pdf', exact:true}).click();
  await expect(page.getByAltText('Source page 1')).toBeVisible();
  await page.locator('.page-tabs button').nth(1).click();
  await expect(page.getByAltText('Source page 2')).toBeVisible();
  expect(submissions).toBe(1);
});

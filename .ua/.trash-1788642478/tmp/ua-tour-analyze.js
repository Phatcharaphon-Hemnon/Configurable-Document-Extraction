const fs = require('fs');

function main() {
  const inputPath = process.argv[2];
  const outputPath = process.argv[3];
  if (!inputPath || !outputPath) {
    console.error('Usage: node ua-tour-analyze.js <input.json> <output.json>');
    process.exit(1);
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  } catch (e) {
    console.error('Failed to read/parse input: ' + e.message);
    process.exit(1);
  }
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const layers = data.layers || [];

  const nodeById = new Map();
  for (const n of nodes) nodeById.set(n.id, n);

  // Fan-in / fan-out over ALL edges (any type)
  const fanIn = new Map();
  const fanOut = new Map();
  for (const n of nodes) { fanIn.set(n.id, 0); fanOut.set(n.id, 0); }
  for (const e of edges) {
    if (fanIn.has(e.target)) fanIn.set(e.target, fanIn.get(e.target) + 1);
    if (fanOut.has(e.source)) fanOut.set(e.source, fanOut.get(e.source) + 1);
  }

  const fanInRanking = [...fanIn.entries()]
    .map(([id, count]) => ({ id, fanIn: count, name: (nodeById.get(id) || {}).name || id }))
    .sort((a, b) => b.fanIn - a.fanIn || a.id.localeCompare(b.id))
    .slice(0, 20);

  const fanOutRanking = [...fanOut.entries()]
    .map(([id, count]) => ({ id, fanOut: count, name: (nodeById.get(id) || {}).name || id }))
    .sort((a, b) => b.fanOut - a.fanOut || a.id.localeCompare(b.id))
    .slice(0, 20);

  // Thresholds for entry scoring
  const fanOutVals = [...fanOut.values()].sort((a, b) => a - b);
  const fanInVals = [...fanIn.values()].sort((a, b) => a - b);
  function percentile(sorted, p) {
    if (sorted.length === 0) return 0;
    const idx = Math.min(sorted.length - 1, Math.floor(p * sorted.length));
    return sorted[idx];
  }
  const fanOutP90 = percentile(fanOutVals, 0.9);
  const fanInP25 = percentile(fanInVals, 0.25);

  const entryNames = new Set(['index.ts','index.js','main.ts','main.js','app.ts','app.js','server.ts','server.js','mod.rs','main.go','main.py','main.rs','manage.py','app.py','wsgi.py','asgi.py','run.py','__main__.py','Application.java','Main.java','Program.cs','config.ru','index.php','App.swift','Application.kt','main.cpp','main.c','main.tsx']);

  const candidates = [];
  for (const n of nodes) {
    let score = 0;
    const fp = n.filePath || '';
    const base = n.name || fp.split('/').pop() || '';
    if (n.type === 'file') {
      if (entryNames.has(base)) score += 3;
      const depth = fp ? fp.split('/').length - 1 : 99;
      if (depth <= 1) score += 1;
      if ((fanOut.get(n.id) || 0) >= fanOutP90 && fanOutP90 > 0) score += 1;
      if ((fanIn.get(n.id) || 0) <= fanInP25) score += 1;
      if (score > 0) candidates.push({ id: n.id, score, name: n.name, summary: n.summary || '' });
    } else if (n.type === 'document') {
      if (base === 'README.md' && (fp === 'README.md' || depth_of(fp) === 0)) {
        score += 5;
      } else if (fp.split('/').length - 1 === 0 && /\.md$/i.test(base)) {
        score += 2;
      }
      if (score > 0) candidates.push({ id: n.id, score, name: n.name, summary: n.summary || '' });
    }
  }
  function depth_of(fp) { return fp.split('/').length - 1; }
  candidates.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  const entryPointCandidates = candidates.slice(0, 5);

  // D. BFS from top code entry point (skip documents)
  const codeCandidates = candidates.filter(c => {
    const n = nodeById.get(c.id);
    return n && n.type !== 'document';
  });
  // Fallback: highest fan-out file if no code candidates
  let startNode = codeCandidates.length > 0 ? codeCandidates[0].id : null;
  if (!startNode) {
    const topFile = fanOutRanking.find(r => (nodeById.get(r.id) || {}).type === 'file');
    startNode = topFile ? topFile.id : (nodes[0] ? nodes[0].id : null);
  }
  const adj = new Map();
  for (const n of nodes) adj.set(n.id, []);
  for (const e of edges) {
    if (e.type === 'imports' || e.type === 'calls') {
      if (adj.has(e.source) && nodeById.has(e.target)) adj.get(e.source).push(e.target);
    }
  }
  const order = [];
  const depthMap = {};
  if (startNode) {
    const visited = new Set([startNode]);
    const queue = [{ id: startNode, d: 0 }];
    depthMap[startNode] = 0;
    while (queue.length > 0) {
      const cur = queue.shift();
      order.push(cur.id);
      const neighbors = (adj.get(cur.id) || []).slice().sort();
      for (const nb of neighbors) {
        if (!visited.has(nb)) {
          visited.add(nb);
          depthMap[nb] = cur.d + 1;
          queue.push({ id: nb, d: cur.d + 1 });
        }
      }
    }
  }
  const byDepth = {};
  for (const [id, d] of Object.entries(depthMap)) {
    const k = String(d);
    if (!byDepth[k]) byDepth[k] = [];
    byDepth[k].push(id);
  }
  for (const k of Object.keys(byDepth)) byDepth[k].sort();

  // E. Non-code inventory
  const documentation = [];
  const infrastructure = [];
  const dataFiles = [];
  const config = [];
  for (const n of nodes) {
    const entry = { id: n.id, name: n.name, type: n.type, summary: n.summary || '' };
    if (n.type === 'document') documentation.push({ id: n.id, name: n.name, summary: n.summary || '' });
    else if (n.type === 'service' || n.type === 'pipeline' || n.type === 'resource') infrastructure.push({ id: n.id, name: n.name, summary: n.summary || '' });
    else if (n.type === 'table' || n.type === 'schema' || n.type === 'endpoint') dataFiles.push({ id: n.id, name: n.name, summary: n.summary || '' });
    else if (n.type === 'config') config.push({ id: n.id, name: n.name, summary: n.summary || '' });
  }
  documentation.sort((a, b) => a.id.localeCompare(b.id));
  infrastructure.sort((a, b) => a.id.localeCompare(b.id));
  dataFiles.sort((a, b) => a.id.localeCompare(b.id));
  config.sort((a, b) => a.id.localeCompare(b.id));

  // F. Tightly coupled clusters (bidirectional imports/calls pairs, expand)
  const pairSet = new Set();
  const fwd = new Set();
  for (const e of edges) {
    if (e.type === 'imports' || e.type === 'calls') fwd.add(e.source + '||' + e.target);
  }
  const bidirPairs = [];
  const seenPair = new Set();
  for (const e of edges) {
    if (e.type === 'imports' || e.type === 'calls') {
      const rev = e.target + '||' + e.source;
      if (fwd.has(rev)) {
        const key = [e.source, e.target].sort().join('||');
        if (!seenPair.has(key)) { seenPair.add(key); bidirPairs.push([e.source, e.target].sort()); }
      }
    }
  }
  // neighbor sets (undirected, imports/calls only)
  const nbrs = new Map();
  for (const n of nodes) nbrs.set(n.id, new Set());
  for (const e of edges) {
    if (e.type === 'imports' || e.type === 'calls') {
      if (nbrs.has(e.source) && nbrs.has(e.target)) {
        nbrs.get(e.source).add(e.target);
        nbrs.get(e.target).add(e.source);
      }
    }
  }
  function countInternalEdges(members) {
    const s = new Set(members);
    let c = 0;
    for (const e of edges) {
      if ((e.type === 'imports' || e.type === 'calls') && s.has(e.source) && s.has(e.target)) c++;
    }
    return c;
  }
  const clusters = [];
  for (const pair of bidirPairs) {
    const members = new Set(pair);
    // expand: add nodes connected to 2+ members
    let changed = true;
    while (changed && members.size < 5) {
      changed = false;
      let best = null;
      for (const n of nodes) {
        if (members.has(n.id)) continue;
        let conn = 0;
        for (const m of members) { if ((nbrs.get(n.id) || new Set()).has(m)) conn++; }
        if (conn >= 2 && members.size < 5) { best = n.id; break; }
      }
      if (best) { members.add(best); changed = true; }
    }
    const arr = [...members].sort();
    const key = arr.join('||');
    if (!pairSet.has(key)) {
      pairSet.add(key);
      clusters.push({ nodes: arr, edgeCount: countInternalEdges(arr) });
    }
    if (clusters.length >= 10) break;
  }
  clusters.sort((a, b) => b.edgeCount - a.edgeCount || a.nodes.join().localeCompare(b.nodes.join()));
  const topClusters = clusters.slice(0, 10);

  // G. layers
  const layerList = layers.map(l => ({ id: l.id, name: l.name, description: l.description || '' }));

  // H. node summary index
  const nodeSummaryIndex = {};
  for (const n of nodes) nodeSummaryIndex[n.id] = { name: n.name, type: n.type, summary: n.summary || '' };

  const out = {
    scriptCompleted: true,
    entryPointCandidates,
    fanInRanking,
    fanOutRanking,
    bfsTraversal: { startNode, order, depthMap, byDepth },
    nonCodeFiles: { documentation, infrastructure, data: dataFiles, config },
    clusters: topClusters,
    layers: { count: layers.length, list: layerList },
    nodeSummaryIndex,
    totalNodes: nodes.length,
    totalEdges: edges.length
  };
  fs.writeFileSync(outputPath, JSON.stringify(out, null, 2), 'utf8');
  process.exit(0);
}

main();

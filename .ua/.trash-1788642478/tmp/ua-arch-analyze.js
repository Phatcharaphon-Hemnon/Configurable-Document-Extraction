const fs = require('fs');

function main() {
  const inputPath = process.argv[2];
  const outputPath = process.argv[3];
  if (!inputPath || !outputPath) {
    console.error('Usage: node ua-arch-analyze.js <input.json> <output.json>');
    process.exit(1);
  }
  let input;
  try {
    input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  } catch (e) {
    console.error('Failed to read/parse input: ' + e.message);
    process.exit(1);
  }
  const fileNodes = input.fileNodes || [];
  const importEdges = input.importEdges || [];
  const allEdges = input.allEdges || [];

  // A. Directory grouping: common prefix then first segment after prefix
  function commonPrefix(paths) {
    if (!paths.length) return '';
    const split = paths.map(p => p.split('/'));
    let prefix = split[0];
    for (const parts of split.slice(1)) {
      let i = 0;
      while (i < prefix.length && i < parts.length && prefix[i] === parts[i]) i++;
      prefix = prefix.slice(0, i);
    }
    return prefix.join('/');
  }
  const paths = fileNodes.map(n => n.filePath || n.id);
  const prefix = commonPrefix(paths);
  const prefixSegs = prefix ? prefix.split('/') : [];
  const directoryGroups = {};
  for (const n of fileNodes) {
    const fp = n.filePath || n.id;
    const segs = fp.split('/');
    let group;
    if (segs.length <= 1) {
      group = 'root';
    } else if (prefixSegs.length > 0) {
      group = segs[prefixSegs.length] || 'root';
      // if file is directly at prefix level (e.g. prefix file), use second-to-last or 'root'
      if (!group || group.includes('.')) group = 'root';
    } else {
      group = segs[0];
    }
    if (!directoryGroups[group]) directoryGroups[group] = [];
    directoryGroups[group].push(n.id);
  }

  // B. Node type grouping
  const nodeTypeGroups = {};
  for (const n of fileNodes) {
    const t = n.type || 'file';
    if (!nodeTypeGroups[t]) nodeTypeGroups[t] = [];
    nodeTypeGroups[t].push(n.id);
  }

  // C. Import adjacency: fan-in/fan-out per file; group-level imports
  const fileFanIn = {};
  const fileFanOut = {};
  for (const n of fileNodes) { fileFanIn[n.id] = 0; fileFanOut[n.id] = 0; }
  const idToGroup = {};
  for (const [g, ids] of Object.entries(directoryGroups)) for (const id of ids) idToGroup[id] = g;
  const groupImportsFrom = {}; // group -> Set of groups it imports from
  const groupImportedBy = {};
  for (const g of Object.keys(directoryGroups)) { groupImportsFrom[g] = new Set(); groupImportedBy[g] = new Set(); }
  for (const e of importEdges) {
    const s = e.source, t = e.target;
    if (s in fileFanOut) fileFanOut[s]++;
    if (t in fileFanIn) fileFanIn[t]++;
    const gs = idToGroup[s], gt = idToGroup[t];
    if (gs && gt && gs !== gt) {
      groupImportsFrom[gs].add(gt);
      groupImportedBy[gt].add(gs);
    }
  }

  // D. Cross-category dependency analysis using allEdges
  const idToType = {};
  for (const n of fileNodes) idToType[n.id] = n.type || 'file';
  const crossMap = {};
  for (const e of allEdges) {
    const st = idToType[e.source] || 'unknown';
    const tt = idToType[e.target] || 'unknown';
    const key = st + '|' + tt + '|' + (e.type || 'unknown');
    crossMap[key] = (crossMap[key] || 0) + 1;
  }
  const crossCategoryEdges = Object.entries(crossMap).map(([k, count]) => {
    const [fromType, toType, edgeType] = k.split('|');
    return { fromType, toType, edgeType, count };
  }).sort((a, b) => b.count - a.count);

  // E. Inter-group import frequency
  const pairCounts = {};
  for (const e of importEdges) {
    const gs = idToGroup[e.source], gt = idToGroup[e.target];
    if (!gs || !gt || gs === gt) continue;
    const key = gs + '|' + gt;
    pairCounts[key] = (pairCounts[key] || 0) + 1;
  }
  const interGroupImports = Object.entries(pairCounts).map(([k, count]) => {
    const [from, to] = k.split('|');
    return { from, to, count };
  }).sort((a, b) => b.count - a.count);

  // F. Intra-group import density
  const intraGroupDensity = {};
  for (const g of Object.keys(directoryGroups)) {
    let internal = 0, total = 0;
    for (const e of importEdges) {
      const gs = idToGroup[e.source], gt = idToGroup[e.target];
      if (gs === g || gt === g) {
        total++;
        if (gs === g && gt === g) internal++;
      }
    }
    intraGroupDensity[g] = { internalEdges: internal, totalEdges: total, density: total ? internal / total : 0 };
  }

  // G. Directory pattern matching
  const dirPatternTable = [
    [/^(routes|api|controllers|endpoints|handlers|routers|blueprints|serializers|controller)$/i, 'api'],
    [/^(services|core|lib|domain|logic|internal|signals|composables|mailers|jobs|channels|agents)$/i, 'service'],
    [/^(models|db|data|persistence|repository|entities|entity|migrations|sql|database|schema)$/i, 'data'],
    [/^(components|views|pages|ui|layouts|screens)$/i, 'ui'],
    [/^(middleware|plugins|interceptors|guards)$/i, 'middleware'],
    [/^(utils|helpers|common|shared|tools|pkg|templatetags)$/i, 'utility'],
    [/^(config|constants|env|settings|management|commands)$/i, 'config'],
    [/^(__tests__|test|tests|spec|specs)$/i, 'test'],
    [/^(types|interfaces|schemas|contracts|dtos|dto|request|response)$/i, 'types'],
    [/^hooks$/i, 'hooks'],
    [/^(store|state|reducers|actions|slices)$/i, 'state'],
    [/^(assets|static|public)$/i, 'assets'],
    [/^(docs|documentation|wiki)$/i, 'documentation'],
    [/^(deploy|deployment|infra|infrastructure|docker|k8s|kubernetes|helm|charts|terraform|tf)$/i, 'infrastructure'],
    [/^(\.github|\.gitlab|\.circleci)$/i, 'ci-cd'],
    [/^(src|app|web|api|scripts)$/i, null]
  ];
  const patternMatches = {};
  for (const g of Object.keys(directoryGroups)) {
    let matched = null;
    for (const [re, label] of dirPatternTable) {
      if (re.test(g)) { matched = label; break; }
    }
    // file-level fallback: if group is generic (src/app/web/api/scripts/root), leave null
    patternMatches[g] = matched;
  }

  // H. Deployment topology
  const allPaths = fileNodes.map(n => n.filePath || '');
  const hasDockerfile = allPaths.some(p => /(^|\/)Dockerfile(\.|$)/.test(p));
  const hasCompose = allPaths.some(p => /docker-compose.*\.yml/.test(p));
  const hasK8s = allPaths.some(p => /\/(k8s|kubernetes|helm)\//.test(p));
  const hasTerraform = allPaths.some(p => /\.tf$/.test(p) || /\.tfvars$/.test(p));
  const hasCI = fileNodes.some(n => n.type === 'pipeline');
  const infraFiles = fileNodes.filter(n => /(Dockerfile|docker-compose|\.github\/workflows|terraform|\.tf$|k8s\/|Makefile|run_all\.sh)/.test(n.filePath || '')).map(n => n.filePath);
  const deploymentTopology = { hasDockerfile, hasCompose, hasK8s, hasTerraform, hasCI, infraFiles };

  // I. Data pipeline detection
  const schemaFiles = fileNodes.filter(n => /\.(sql|graphql|gql|proto|prisma)$/.test(n.filePath || '') || /field_catalog/.test(n.filePath || '')).map(n => n.filePath);
  const migrationFiles = fileNodes.filter(n => /migrations?\//.test(n.filePath || '')).map(n => n.filePath);
  const dataModelFiles = fileNodes.filter(n => /(models|repository|schemas\/documents|extraction\.db)/.test(n.filePath || '')).map(n => n.filePath);
  const apiHandlerFiles = fileNodes.filter(n => /(routes|routers|controllers|api\/client)/.test(n.filePath || '')).map(n => n.filePath);
  const dataPipeline = { schemaFiles, migrationFiles, dataModelFiles, apiHandlerFiles };

  // J. Documentation coverage
  const docGroups = new Set();
  // groups containing a doc node
  for (const n of fileNodes) {
    if (n.type === 'document' && idToGroup[n.id]) docGroups.add(idToGroup[n.id]);
  }
  // docs/ group itself counts
  const totalGroups = Object.keys(directoryGroups).length;
  const undocumentedGroups = Object.keys(directoryGroups).filter(g => !docGroups.has(g));
  const docCoverage = { groupsWithDocs: docGroups.size, totalGroups, coverageRatio: totalGroups ? docGroups.size / totalGroups : 0, undocumentedGroups };

  // K. Dependency direction
  const dirPairs = {};
  for (const e of importEdges) {
    const gs = idToGroup[e.source], gt = idToGroup[e.target];
    if (!gs || !gt || gs === gt) continue;
    const key = [gs, gt].sort().join('|');
    if (!dirPairs[key]) dirPairs[key] = {};
    const fwd = gs + '->' + gt;
    dirPairs[key][fwd] = (dirPairs[key][fwd] || 0) + 1;
  }
  const dependencyDirection = [];
  for (const [key, counts] of Object.entries(dirPairs)) {
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    if (entries.length === 1) {
      const [dir] = entries[0][0].split('->');
      const other = key.split('|').find(x => x !== dir);
      dependencyDirection.push({ dependent: entries[0][0].split('->')[0], dependsOn: entries[0][0].split('->')[1] });
    } else {
      const [top] = entries[0][0].split('->');
      const [topDir, topTarget] = entries[0][0].split('->');
      dependencyDirection.push({ dependent: topDir, dependsOn: topTarget });
    }
  }

  // fileStats
  const filesPerGroup = {};
  for (const [g, ids] of Object.entries(directoryGroups)) filesPerGroup[g] = ids.length;
  const nodeTypeCounts = {};
  for (const n of fileNodes) nodeTypeCounts[n.type || 'file'] = (nodeTypeCounts[n.type || 'file'] || 0) + 1;

  const result = {
    scriptCompleted: true,
    commonPrefix: prefix,
    directoryGroups,
    nodeTypeGroups,
    crossCategoryEdges,
    interGroupImports,
    intraGroupDensity,
    patternMatches,
    deploymentTopology,
    dataPipeline,
    docCoverage,
    dependencyDirection,
    fileStats: { totalFileNodes: fileNodes.length, filesPerGroup, nodeTypeCounts },
    fileFanIn,
    fileFanOut
  };
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  console.log('OK groups=' + Object.keys(directoryGroups).length + ' nodes=' + fileNodes.length);
}
main();

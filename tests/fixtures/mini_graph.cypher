// Mini graph shared by every test (expected answers: tests/fixtures/README.md).
// Fictional companies and numbers: they only exist to make tests deterministic.
// Loaded by tests/conftest.py on a wiped test database. Never edit an existing value:
// other roles' tests depend on it. Add new nodes in a new statement at the end if needed (role A).

UNWIND [
  {code: 'FR', name: 'France', eu: true}, {code: 'NL', name: 'Netherlands', eu: true},
  {code: 'DE', name: 'Germany', eu: true}, {code: 'US', name: 'United States', eu: false}
] AS row
CREATE (:Country {code: row.code, name: row.name, eu_eea: row.eu});

UNWIND [
  {code: 'ENERGY', name: 'Energy'}, {code: 'INDUSTRIALS', name: 'Industrials'},
  {code: 'CONS_DISC', name: 'Consumer Discretionary'}, {code: 'TECH', name: 'Information Technology'},
  {code: 'FINANCIALS', name: 'Financials'}, {code: 'MATERIALS', name: 'Materials'}
] AS row
CREATE (:Sector {code: row.code, name: row.name});

UNWIND [
  {code: 'OIL_GAS', name: 'Oil & Gas', sector: 'ENERGY'}, {code: 'AIRLINES', name: 'Airlines', sector: 'INDUSTRIALS'},
  {code: 'LUXURY', name: 'Luxury goods', sector: 'CONS_DISC'}, {code: 'AUTO', name: 'Automobiles', sector: 'CONS_DISC'},
  {code: 'SEMIS', name: 'Semiconductors', sector: 'TECH'}, {code: 'SOFTWARE', name: 'Software', sector: 'TECH'},
  {code: 'BANKS', name: 'Banks', sector: 'FINANCIALS'}, {code: 'MINING', name: 'Metals & Mining', sector: 'MATERIALS'},
  {code: 'CHEMICALS', name: 'Chemicals', sector: 'MATERIALS'}
] AS row
MATCH (s:Sector {code: row.sector})
CREATE (:Industry {code: row.code, name: row.name})-[:PART_OF]->(s);

CREATE (:Factor:Commodity {code: 'BRENT', name: 'Brent crude oil', kind: 'commodity', unit: 'USD/bbl', source: 'yahoo', source_id: 'BZ=F'})
CREATE (:Factor:Commodity {code: 'COPPER', name: 'Copper', kind: 'commodity', unit: 'USD/lb', source: 'yahoo', source_id: 'HG=F'})
CREATE (:Factor:Commodity {code: 'GOLD', name: 'Gold', kind: 'commodity', unit: 'USD/oz', source: 'yahoo', source_id: 'GC=F'})
CREATE (:Factor:Currency {code: 'EURUSD', name: 'Euro / US dollar', kind: 'currency', unit: 'USD per EUR', source: 'yahoo', source_id: 'EURUSD=X'})
CREATE (:Factor:Rate {code: 'EUR10Y', name: 'Euro area 10-year AAA yield', kind: 'rate', unit: '%', source: 'ecb', source_id: 'YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y'});

UNWIND [
  {isin: 'FRTEST000011', name: 'Petrolia SA', ticker: 'PTL.PA', industry: 'OIL_GAS', country: 'FR', cap: 120.0e9, pea: true},
  {isin: 'FRTEST000029', name: 'AeroLys', ticker: 'AEL.PA', industry: 'AIRLINES', country: 'FR', cap: 4.0e9, pea: true},
  {isin: 'FRTEST000037', name: 'Maison Varenne', ticker: 'VAR.PA', industry: 'LUXURY', country: 'FR', cap: 300.0e9, pea: true},
  {isin: 'NLTEST000045', name: 'Siliqa NV', ticker: 'SLQ.AS', industry: 'SEMIS', country: 'NL', cap: 250.0e9, pea: true},
  {isin: 'FRTEST000052', name: 'Banque Orsay', ticker: 'ORS.PA', industry: 'BANKS', country: 'FR', cap: 45.0e9, pea: true},
  {isin: 'DETEST000060', name: 'Autoroute Motoren AG', ticker: 'AUT.DE', industry: 'AUTO', country: 'DE', cap: 60.0e9, pea: true},
  {isin: 'DETEST000078', name: 'Cuprum Bergbau AG', ticker: 'CUP.DE', industry: 'MINING', country: 'DE', cap: 15.0e9, pea: true},
  {isin: 'FRTEST000086', name: 'Helios Chimie', ticker: 'HEL.PA', industry: 'CHEMICALS', country: 'FR', cap: 8.0e9, pea: true},
  {isin: 'USTEST000094', name: 'Vela Software Inc', ticker: 'VELA', industry: 'SOFTWARE', country: 'US', cap: 900.0e9, pea: false}
] AS row
MATCH (n:Industry {code: row.industry}), (k:Country {code: row.country})
CREATE (c:Company {isin: row.isin, name: row.name, ticker: row.ticker, market_cap_eur: row.cap,
                   pea_eligible: row.pea, currency: 'EUR'})
CREATE (c)-[:IN_INDUSTRY]->(n)
CREATE (c)-[:HEADQUARTERED_IN]->(k);

CREATE (:Index {code: 'IDX_FR40', name: 'Fixture France 40', provider: 'fixture'})
CREATE (:Index {code: 'IDX_WORLD', name: 'Fixture World', provider: 'fixture'});

UNWIND [
  {idx: 'IDX_FR40', isin: 'FRTEST000011', w: 0.30}, {idx: 'IDX_FR40', isin: 'FRTEST000037', w: 0.25},
  {idx: 'IDX_FR40', isin: 'FRTEST000052', w: 0.20}, {idx: 'IDX_FR40', isin: 'FRTEST000086', w: 0.15},
  {idx: 'IDX_FR40', isin: 'FRTEST000029', w: 0.10},
  {idx: 'IDX_WORLD', isin: 'USTEST000094', w: 0.40}, {idx: 'IDX_WORLD', isin: 'NLTEST000045', w: 0.20},
  {idx: 'IDX_WORLD', isin: 'FRTEST000037', w: 0.15}, {idx: 'IDX_WORLD', isin: 'FRTEST000011', w: 0.15},
  {idx: 'IDX_WORLD', isin: 'DETEST000078', w: 0.10}
] AS row
MATCH (i:Index {code: row.idx}), (c:Company {isin: row.isin})
CREATE (i)-[:CONSTITUENT {weight: row.w, as_of: date('2026-10-22')}]->(c);

UNWIND [
  {isin: 'FRFUND000011', name: 'Fixture France 40 UCITS ETF', ticker: 'FX40.PA', ter: 0.25, rep: 'physical', pea: true, idx: 'IDX_FR40'},
  {isin: 'FRFUND000029', name: 'Fixture World PEA UCITS ETF', ticker: 'FXWP.PA', ter: 0.38, rep: 'synthetic', pea: true, idx: 'IDX_WORLD'},
  {isin: 'IEFUND000037', name: 'Fixture World Physical UCITS ETF', ticker: 'FXWD.AS', ter: 0.20, rep: 'physical', pea: false, idx: 'IDX_WORLD'}
] AS row
MATCH (i:Index {code: row.idx})
CREATE (f:Fund {isin: row.isin, name: row.name, ticker: row.ticker, ter: row.ter, replication: row.rep,
                pea_eligible: row.pea, issuer: 'Fixture AM'})
CREATE (f)-[:TRACKS]->(i);

// Physical funds hold their index; the synthetic PEA fund holds a European substitute basket.
MATCH (f:Fund)-[:TRACKS]->(:Index)-[r:CONSTITUENT]->(c:Company)
WHERE f.replication = 'physical'
CREATE (f)-[:HOLDS {weight: r.weight, as_of: r.as_of}]->(c);

UNWIND [{isin: 'FRTEST000052', w: 0.5}, {isin: 'FRTEST000086', w: 0.5}] AS row
MATCH (f:Fund {isin: 'FRFUND000029'}), (c:Company {isin: row.isin})
CREATE (f)-[:HOLDS {weight: row.w, as_of: date('2026-10-22')}]->(c);

UNWIND [
  {ind: 'OIL_GAS', rel: 'PRODUCES', factor: 'BRENT'}, {ind: 'AIRLINES', rel: 'CONSUMES', factor: 'BRENT'},
  {ind: 'CHEMICALS', rel: 'CONSUMES', factor: 'BRENT'}, {ind: 'MINING', rel: 'PRODUCES', factor: 'COPPER'},
  {ind: 'AUTO', rel: 'CONSUMES', factor: 'COPPER'}
] AS row
MATCH (n:Industry {code: row.ind}), (x:Factor {code: row.factor})
FOREACH (_ IN CASE WHEN row.rel = 'PRODUCES' THEN [1] ELSE [] END | CREATE (n)-[:PRODUCES {source: 'sector_map'}]->(x))
FOREACH (_ IN CASE WHEN row.rel = 'CONSUMES' THEN [1] ELSE [] END | CREATE (n)-[:CONSUMES {source: 'sector_map'}]->(x));

MATCH (c:Company {isin: 'NLTEST000045'}), (x:Factor {code: 'COPPER'})
CREATE (c)-[:CONSUMES {source: 'wikidata'}]->(x);

UNWIND [
  {isin: 'FRTEST000011', factor: 'BRENT', beta: 0.62, r2: 0.48, t: 11.9},
  {isin: 'FRTEST000029', factor: 'BRENT', beta: -0.41, r2: 0.22, t: -6.6},
  {isin: 'FRTEST000086', factor: 'BRENT', beta: -0.15, r2: 0.05, t: -2.8},
  {isin: 'DETEST000078', factor: 'COPPER', beta: 0.90, r2: 0.55, t: 13.7},
  {isin: 'DETEST000060', factor: 'COPPER', beta: -0.20, r2: 0.08, t: -3.6},
  {isin: 'NLTEST000045', factor: 'COPPER', beta: 0.10, r2: 0.02, t: 1.7},
  {isin: 'FRTEST000037', factor: 'EURUSD', beta: -0.80, r2: 0.30, t: -8.1},
  {isin: 'NLTEST000045', factor: 'EURUSD', beta: -0.35, r2: 0.12, t: -4.6},
  {isin: 'FRTEST000052', factor: 'EUR10Y', beta: 0.55, r2: 0.25, t: 7.1}
] AS row
MATCH (c:Company {isin: row.isin}), (x:Factor {code: row.factor})
CREATE (c)-[:SENSITIVE_TO {beta: row.beta, r2: row.r2, t_stat: row.t, n_obs: 156,
                           window_start: date('2023-10-20'), window_end: date('2026-10-16'),
                           method: 'ols_market_controlled'}]->(x);

UNWIND [
  {a: 'DETEST000078', b: 'FRTEST000011', rho: 0.55}, {a: 'DETEST000060', b: 'DETEST000078', rho: 0.40},
  {a: 'FRTEST000029', b: 'FRTEST000086', rho: 0.45}, {a: 'FRTEST000037', b: 'NLTEST000045', rho: 0.50},
  {a: 'NLTEST000045', b: 'USTEST000094', rho: 0.60}, {a: 'FRTEST000011', b: 'FRTEST000052', rho: 0.35}
] AS row
MATCH (a:Company {isin: row.a}), (b:Company {isin: row.b})
CREATE (a)-[:CORRELATED_WITH {rho: row.rho, window_end: date('2026-10-16')}]->(b);

MATCH (c:Company {isin: 'DETEST000060'}), (x:Factor {code: 'EURUSD'})
CREATE (c)-[:PREDICTED_SENSITIVITY {prob: 0.71, direction: 'down', model_version: 'fixture-v0'}]->(x);

UNWIND [
  {url: 'https://example.org/news/cuprum-autoroute-contract', title: 'Cuprum signs multi-year copper supply deal with Autoroute', date: '2026-09-14'},
  {url: 'https://example.org/news/petrolia-helios-feedstock', title: 'Petrolia to supply naphtha to Helios Chimie', date: '2026-08-02'},
  {url: 'https://example.org/news/vela-varenne-rumour', title: 'Rumour: Vela in talks to equip Maison Varenne boutiques', date: '2026-07-01'}
] AS row
CREATE (:Article {url: row.url, title: row.title, source: 'example.org', published_at: date(row.date)});

UNWIND [
  {url: 'https://example.org/news/cuprum-autoroute-contract', ref: 'DETEST000078'},
  {url: 'https://example.org/news/cuprum-autoroute-contract', ref: 'DETEST000060'},
  {url: 'https://example.org/news/petrolia-helios-feedstock', ref: 'FRTEST000011'},
  {url: 'https://example.org/news/petrolia-helios-feedstock', ref: 'FRTEST000086'},
  {url: 'https://example.org/news/vela-varenne-rumour', ref: 'USTEST000094'},
  {url: 'https://example.org/news/vela-varenne-rumour', ref: 'FRTEST000037'}
] AS row
MATCH (a:Article {url: row.url}), (c:Company {isin: row.ref})
CREATE (a)-[:MENTIONS]->(c);

MATCH (a:Article {url: 'https://example.org/news/cuprum-autoroute-contract'}), (x:Factor {code: 'COPPER'})
CREATE (a)-[:MENTIONS]->(x);

UNWIND [
  {id: 'c1', s: 'DETEST000078', o: 'DETEST000060', conf: 0.80, status: 'hypothesis', url: 'https://example.org/news/cuprum-autoroute-contract',
   sentence: 'Cuprum will deliver copper cathodes to Autoroute plants from 2027.'},
  {id: 'c2', s: 'FRTEST000011', o: 'FRTEST000086', conf: 0.60, status: 'confirmed', url: 'https://example.org/news/petrolia-helios-feedstock',
   sentence: 'Petrolia will supply naphtha feedstock to Helios Chimie.'},
  {id: 'c3', s: 'NLTEST000045', o: 'DETEST000060', conf: 0.30, status: 'hypothesis', url: 'https://example.org/news/cuprum-autoroute-contract',
   sentence: 'Autoroute is also said to source chips from Siliqa.'},
  {id: 'c4', s: 'USTEST000094', o: 'FRTEST000037', conf: 0.20, status: 'rejected', url: 'https://example.org/news/vela-varenne-rumour',
   sentence: 'Vela may equip Maison Varenne boutiques.'}
] AS row
MATCH (s:Company {isin: row.s}), (o:Company {isin: row.o}), (a:Article {url: row.url})
CREATE (q:Claim {claim_id: row.id, kind: 'SUPPLIES', confidence: row.conf, status: row.status,
                 sentence: row.sentence, model: 'fixture'})
CREATE (q)-[:SUBJECT]->(s)
CREATE (q)-[:OBJECT]->(o)
CREATE (q)-[:EXTRACTED_FROM]->(a);

// Constraints and indexes: part of the contract (docs/schema.md). Add, never rename or drop.
// Every statement is idempotent (IF NOT EXISTS). Run with: python -m scripts.cypher_files scripts/constraints.cypher

CREATE CONSTRAINT company_isin IF NOT EXISTS FOR (c:Company) REQUIRE c.isin IS UNIQUE;
CREATE CONSTRAINT fund_isin IF NOT EXISTS FOR (f:Fund) REQUIRE f.isin IS UNIQUE;
CREATE CONSTRAINT index_code IF NOT EXISTS FOR (i:Index) REQUIRE i.code IS UNIQUE;
CREATE CONSTRAINT sector_code IF NOT EXISTS FOR (s:Sector) REQUIRE s.code IS UNIQUE;
CREATE CONSTRAINT industry_code IF NOT EXISTS FOR (n:Industry) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT country_code IF NOT EXISTS FOR (k:Country) REQUIRE k.code IS UNIQUE;
CREATE CONSTRAINT factor_code IF NOT EXISTS FOR (x:Factor) REQUIRE x.code IS UNIQUE;
CREATE CONSTRAINT article_url IF NOT EXISTS FOR (a:Article) REQUIRE a.url IS UNIQUE;
CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (q:Claim) REQUIRE q.claim_id IS UNIQUE;
// Technical lock nodes (role C): same names as the constraints the code creates, so both stay idempotent.
// GdsRefreshLock (app/services/gds.py): deleted after each refresh; one left by a crash is taken over after 30 min.
CREATE CONSTRAINT gds_refresh_lock IF NOT EXISTS FOR (lock:GdsRefreshLock) REQUIRE lock.name IS UNIQUE;
// MlWriteLock (ml/train.py): created and deleted inside the transaction that writes the predictions.
CREATE CONSTRAINT ml_write_lock_name IF NOT EXISTS FOR (lock:MlWriteLock) REQUIRE lock.name IS UNIQUE;

CREATE INDEX company_ticker IF NOT EXISTS FOR (c:Company) ON (c.ticker);
CREATE INDEX company_pea IF NOT EXISTS FOR (c:Company) ON (c.pea_eligible);
CREATE INDEX fund_ticker IF NOT EXISTS FOR (f:Fund) ON (f.ticker);
CREATE INDEX claim_status IF NOT EXISTS FOR (q:Claim) ON (q.status);
// Range index for the paginated company search (ORDER BY market_cap_eur DESC, cursor on cap + isin).
CREATE INDEX company_market_cap IF NOT EXISTS FOR (c:Company) ON (c.market_cap_eur);
CREATE INDEX sensitive_r2 IF NOT EXISTS FOR ()-[r:SENSITIVE_TO]-() ON (r.r2);

CREATE FULLTEXT INDEX company_name_ft IF NOT EXISTS FOR (c:Company) ON EACH [c.name, c.ticker];
CREATE FULLTEXT INDEX fund_name_ft IF NOT EXISTS FOR (f:Fund) ON EACH [f.name, f.ticker];
CREATE FULLTEXT INDEX factor_name_ft IF NOT EXISTS FOR (x:Factor) ON EACH [x.name, x.code];

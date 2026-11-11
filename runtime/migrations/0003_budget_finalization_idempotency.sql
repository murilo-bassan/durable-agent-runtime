-- One reservation may have only one terminal release, even when a resumed
-- worker repeats settle/release. Historical entries remain valid without IDs.
ALTER TABLE token_ledger ADD COLUMN reservation_id BIGINT REFERENCES token_ledger (id);
CREATE UNIQUE INDEX token_ledger_finalization_once
    ON token_ledger (reservation_id) WHERE kind = 'released';

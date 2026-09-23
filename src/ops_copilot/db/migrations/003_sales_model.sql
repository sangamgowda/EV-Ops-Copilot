-- Most sales rows are not linked to a tracked vehicle, so the model
-- and city sold are recorded on the transaction itself. Without
-- them "which model sold best in Pune" has no answer.

ALTER TABLE sales_transactions ADD COLUMN IF NOT EXISTS model_code TEXT;
ALTER TABLE sales_transactions ADD COLUMN IF NOT EXISTS city TEXT;
CREATE INDEX IF NOT EXISTS idx_sales_model ON sales_transactions (model_code, sold_on DESC);

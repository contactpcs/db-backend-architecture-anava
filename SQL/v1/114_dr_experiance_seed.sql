ALTER TABLE doctors
  ADD COLUMN IF NOT EXISTS years_of_experience INTEGER
  CHECK (years_of_experience BETWEEN 0 AND 60);

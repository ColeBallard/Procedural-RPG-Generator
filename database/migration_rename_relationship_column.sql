-- Migration: Rename CharacterRelationships.relationship to relationship_type
-- Run this if you have an existing database created from the original DDL
-- where the column was named `relationship`. The application's startup
-- hook (app/startup.py:rename_columns) performs the same rename
-- automatically when AUTO_MIGRATE is enabled; this script is provided
-- for ops who manage schema manually.
--
-- Requires MySQL >= 8.0 or SQLite >= 3.25 for RENAME COLUMN support.

ALTER TABLE `CharacterRelationships`
  RENAME COLUMN `relationship` TO `relationship_type`;

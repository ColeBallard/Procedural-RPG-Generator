-- Migration: Add NameLibrary table and Seeds.naming_themes column
-- Run this if you have an existing database without name-library support.

ALTER TABLE `Seeds`
  ADD COLUMN `naming_themes` text AFTER `current_turn`;

CREATE TABLE IF NOT EXISTS `NameLibrary` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `source` varchar(32) NOT NULL,
  `theme` varchar(64) NOT NULL,
  `gender` varchar(16) NOT NULL DEFAULT 'any',
  `category` varchar(16) NOT NULL DEFAULT 'first',
  `name` varchar(128) NOT NULL,
  `meaning` text,
  `origin` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_namelib_source` (`source`),
  KEY `idx_namelib_theme` (`theme`),
  KEY `idx_namelib_gender` (`gender`),
  KEY `idx_namelib_category` (`category`),
  KEY `idx_namelib_lookup` (`source`, `theme`, `gender`, `category`)
);

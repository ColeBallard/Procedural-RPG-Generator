-- Migration: Add TranscriptEntries table
-- Run this if you have an existing database without transcript-log support.

CREATE TABLE IF NOT EXISTS `TranscriptEntries` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `turn` int DEFAULT NULL,
  `kind` varchar(32) NOT NULL,
  `speaker` varchar(64) DEFAULT NULL,
  `text` text NOT NULL,
  `meta` text,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_transcript_seed_id` (`seed_id`, `id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`)
);

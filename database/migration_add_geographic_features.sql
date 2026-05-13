-- Migration: Add GeographicFeatures table for region/line geography
-- (forests, mountain ranges, rivers, lakes, hills, plains, swamps,
-- deserts, coasts) generated alongside the settlement layout. Run this
-- on databases created before the geography pass was introduced.

CREATE TABLE IF NOT EXISTS `GeographicFeatures` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `name` varchar(128) DEFAULT NULL,
  `type` varchar(32) DEFAULT 'forest',
  `description` text,
  `geometry` text,
  `closed` tinyint(1) DEFAULT 1,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_geofeat_seed_id` (`seed_id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`)
);

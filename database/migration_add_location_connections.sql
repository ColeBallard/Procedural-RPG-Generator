-- Migration: Add LocationConnections table for road/path edges between
-- top-level settlements. Run this on databases created before the map UI
-- was introduced.

CREATE TABLE IF NOT EXISTS `LocationConnections` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `from_location_id` int unsigned NOT NULL,
  `to_location_id` int unsigned NOT NULL,
  `name` varchar(128) DEFAULT NULL,
  `type` varchar(32) DEFAULT 'road',
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_locconn_seed_id` (`seed_id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`from_location_id`) REFERENCES `Locations`(`id`),
  FOREIGN KEY (`to_location_id`) REFERENCES `Locations`(`id`)
);

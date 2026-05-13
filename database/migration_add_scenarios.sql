-- Migration: Add Scenarios + ScenarioParticipants tables
-- Run this if you have an existing database without scenario support
-- (battle, dialogue, trade, ... structured mid-turn interactions).

CREATE TABLE IF NOT EXISTS `Scenarios` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `kind` varchar(32) NOT NULL,
  `status` varchar(16) NOT NULL DEFAULT 'active',
  `state` text,
  `summary` text,
  `turn_started` int DEFAULT NULL,
  `turn_ended` int DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_scenarios_seed_status` (`seed_id`, `status`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`)
);

CREATE TABLE IF NOT EXISTS `ScenarioParticipants` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `scenario_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `role` varchar(32) NOT NULL DEFAULT 'participant',
  `order_index` smallint DEFAULT 0,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_scenario_participants_scenario` (`scenario_id`),
  FOREIGN KEY (`scenario_id`) REFERENCES `Scenarios`(`id`) ON DELETE CASCADE,
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`)
);

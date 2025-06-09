-- CharacterItems definition

CREATE TABLE `CharacterItems` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `item_id` int unsigned NOT NULL,
  `quantity` int unsigned DEFAULT NULL,
  `condition` float DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`item_id`) REFERENCES `Items`(`id`)
); 

-- CharacterQuests definition

CREATE TABLE `CharacterQuests` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `quest_id` int unsigned NOT NULL,
  `progress` float DEFAULT NULL,
  `current_step` smallint unsigned DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`quest_id`) REFERENCES `Quests`(`id`)
);

-- CharacterRelationships definition

CREATE TABLE `CharacterRelationships` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `related_character_id` int unsigned NOT NULL,
  `relationship` varchar(64) DEFAULT NULL,
  `attraction` smallint DEFAULT 5,
  `respect` smallint DEFAULT 5,
  `trust` smallint DEFAULT 5,
  `familiarity` smallint DEFAULT 0,
  `anger` smallint DEFAULT 5,
  `fear` smallint DEFAULT 5,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`related_character_id`) REFERENCES `Characters`(`id`),
  CHECK (`attraction` BETWEEN 1 AND 10),
  CHECK (`respect` BETWEEN 1 AND 10),
  CHECK (`trust` BETWEEN 1 AND 10),
  CHECK (`familiarity` BETWEEN 1 AND 10),
  CHECK (`anger` BETWEEN 1 AND 10),
  CHECK (`fear` BETWEEN 1 AND 10)
);

-- CharacterSkills definition

CREATE TABLE `CharacterSkills` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `skill_id` int unsigned NOT NULL,
  `level` smallint unsigned DEFAULT NULL,
  `exp_points` bigint unsigned DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`skill_id`) REFERENCES `Skills`(`id`)
); 

-- CharacterStatuses definition

CREATE TABLE `CharacterStatuses` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `status_id` int unsigned NOT NULL,
  `active` tinyint(1) DEFAULT NULL,
  `end_date_time` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`status_id`) REFERENCES `Statuses`(`id`)
); 

-- Characters definition

CREATE TABLE `Characters` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `main_character` tinyint(1) DEFAULT NULL,
  `alive` tinyint(1) DEFAULT NULL,
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `date_of_birth` datetime DEFAULT NULL,
  `race` varchar(64) DEFAULT NULL,
  `gender` tinyint(1) DEFAULT NULL,
  `level` smallint unsigned DEFAULT NULL,
  `exp_points` bigint unsigned DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `strength` smallint unsigned DEFAULT NULL,
  `speed` smallint unsigned DEFAULT NULL,
  `agility` smallint unsigned DEFAULT NULL,
  `intelligence` smallint unsigned DEFAULT NULL,
  `wisdom` smallint unsigned DEFAULT NULL,
  `charisma` smallint unsigned DEFAULT NULL,
  `current_health` int unsigned DEFAULT NULL,
  `max_health` int unsigned DEFAULT NULL,
  `current_currency` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`)
); 

-- EventCharacters definition

CREATE TABLE `EventCharacters` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `character_id` int unsigned NOT NULL,
  `event_id` int unsigned NOT NULL,
  `role` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`character_id`) REFERENCES `Characters`(`id`),
  FOREIGN KEY (`event_id`) REFERENCES `Events`(`id`)
); 

-- Events definition

CREATE TABLE `Events` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `description` text,
  `start_date_time` datetime DEFAULT NULL,
  `type` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `location_id` int unsigned NOT NULL,
  `end_date_time` datetime DEFAULT NULL,
  `start_turn` int unsigned DEFAULT NULL,
  `end_turn` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`location_id`) REFERENCES `Locations`(`id`)
); 

-- Items definition

CREATE TABLE `Items` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) DEFAULT NULL,
  `description` text,
  `type` varchar(64) DEFAULT NULL,
  `value` float DEFAULT NULL,
  `weight` float DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
); 

-- Locations definition

CREATE TABLE `Locations` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci DEFAULT NULL,
  `description` text,
  `longitude` float DEFAULT NULL,
  `latitude` float DEFAULT NULL,
  `type` varchar(64) DEFAULT NULL,
  `climate` varchar(32) DEFAULT NULL,
  `terrain` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `parent_id` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`parent_id`) REFERENCES `Locations`(`id`)
); 

-- QuestSteps definition

CREATE TABLE `QuestSteps` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `seed_id` int unsigned NOT NULL,
  `quest_id` int unsigned NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime DEFAULT NULL,
  `order` smallint unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`),
  FOREIGN KEY (`quest_id`) REFERENCES `Quests`(`id`)
); 

-- Quests definition

CREATE TABLE `Quests` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(128) DEFAULT NULL,
  `description` text,
  `start_date_time` datetime DEFAULT NULL,
  `end_date_time` datetime DEFAULT NULL,
  `start_turn` int unsigned DEFAULT NULL,
  `end_turn` int unsigned DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `seed_id` int unsigned NOT NULL,
  `currency_reward` int unsigned DEFAULT NULL,
  `exp_reward` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`seed_id`) REFERENCES `Seeds`(`id`)
); 

-- Seeds definition

CREATE TABLE `Seeds` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `current_date_time` datetime DEFAULT NULL,
  `current_turn` int unsigned DEFAULT NULL,
  PRIMARY KEY (`id`)
); 

-- Skills definition

CREATE TABLE `Skills` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) DEFAULT NULL,
  `description` text,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
); 

-- Statuses definition

CREATE TABLE `Statuses` (
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) DEFAULT NULL,
  `description` text,
  `type` varchar(64) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `duration` float DEFAULT NULL,
  PRIMARY KEY (`id`)
); 

-- Steps definition

CREATE TABLE `Steps` (
  `name` varchar(64) DEFAULT NULL,
  `id` int unsigned NOT NULL AUTO_INCREMENT,
  `description` text,
  `location_id` int unsigned DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  FOREIGN KEY (`location_id`) REFERENCES `Locations`(`id`)
); 

-- Migration: bind every Seed to its owning User.
--
-- Adds a nullable Seeds.user_id column referencing Users.id so the per-seed
-- routes can enforce ownership and stop returning every user's saved games
-- to every authenticated caller (BOLA / IDOR fix).
--
-- Existing rows keep ``user_id = NULL``; the application treats those as
-- inaccessible when LOGIN_REQUIRED is on, so legacy seeds are not exposed
-- to other users. To preserve a specific legacy seed, run:
--
--     UPDATE Seeds SET user_id = <id> WHERE id = <seed_id>;
--
-- after this migration.

ALTER TABLE `Seeds`
    ADD COLUMN `user_id` int unsigned DEFAULT NULL,
    ADD KEY `idx_seeds_user_id` (`user_id`),
    ADD CONSTRAINT `fk_seeds_user_id`
        FOREIGN KEY (`user_id`) REFERENCES `Users`(`id`);

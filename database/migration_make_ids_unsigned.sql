-- Make every `id` / `*_id` column INT UNSIGNED to match database/ddl.sql.
--
-- Why: the canonical schema (database/ddl.sql) declares every primary key and
-- foreign key as `int unsigned`, and the newer ORM models (LocationConnection,
-- GeographicFeature, TranscriptEntry) declare their FK columns the same way
-- via app/orm.py's UnsignedInt variant. Databases that were bootstrapped by
-- Base.metadata.create_all() in earlier versions ended up with signed `INT`
-- columns instead, which makes MySQL reject the new FKs with errno 3780
-- ("Referencing column ... and referenced column ... are incompatible").
--
-- This migration is idempotent: it only flips columns that aren't already
-- unsigned, so it's safe to run repeatedly and is a no-op on a database that
-- was bootstrapped from ddl.sql.
--
-- Apply with e.g.:
--   mysql -u <user> -p <database> < database/migration_make_ids_unsigned.sql

DELIMITER $$

DROP PROCEDURE IF EXISTS prg_make_ids_unsigned$$
CREATE PROCEDURE prg_make_ids_unsigned()
BEGIN
  -- Snapshot every FK in the current schema so we can recreate them after the
  -- column-type changes. We only handle single-column FKs; this codebase has
  -- no composite FKs.
  DROP TEMPORARY TABLE IF EXISTS _fk_snapshot;
  CREATE TEMPORARY TABLE _fk_snapshot AS
    SELECT
      kcu.TABLE_NAME      AS table_name,
      kcu.CONSTRAINT_NAME AS constraint_name,
      kcu.COLUMN_NAME     AS column_name,
      kcu.REFERENCED_TABLE_NAME  AS ref_table,
      kcu.REFERENCED_COLUMN_NAME AS ref_column
    FROM information_schema.KEY_COLUMN_USAGE kcu
    WHERE kcu.TABLE_SCHEMA = DATABASE()
      AND kcu.REFERENCED_TABLE_NAME IS NOT NULL;

  -- 1) Drop every FK so column types can be altered freely.
  BEGIN
    DECLARE v_done       INT DEFAULT 0;
    DECLARE v_table      VARCHAR(128);
    DECLARE v_constraint VARCHAR(128);
    DECLARE fk_cur CURSOR FOR
      SELECT DISTINCT table_name, constraint_name FROM _fk_snapshot;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    OPEN fk_cur;
    drop_loop: LOOP
      FETCH fk_cur INTO v_table, v_constraint;
      IF v_done THEN LEAVE drop_loop; END IF;
      SET @s = CONCAT('ALTER TABLE `', v_table,
                      '` DROP FOREIGN KEY `', v_constraint, '`');
      PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE fk_cur;
  END;

  -- 2) Flip every signed INT id / *_id column to UNSIGNED, preserving
  --    nullability and AUTO_INCREMENT. The "|" ESCAPE makes the LIKE pattern
  --    match a literal underscore (otherwise MySQL treats `_` as a wildcard).
  BEGIN
    DECLARE v_done     INT DEFAULT 0;
    DECLARE v_table    VARCHAR(128);
    DECLARE v_col      VARCHAR(128);
    DECLARE v_nullable VARCHAR(8);
    DECLARE v_extra    VARCHAR(64);
    DECLARE col_cur CURSOR FOR
      SELECT TABLE_NAME, COLUMN_NAME, IS_NULLABLE, EXTRA
        FROM information_schema.COLUMNS
       WHERE TABLE_SCHEMA = DATABASE()
         AND DATA_TYPE = 'int'
         AND (COLUMN_NAME = 'id' OR COLUMN_NAME LIKE '%|_id' ESCAPE '|')
         AND LOCATE('unsigned', COLUMN_TYPE) = 0;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    OPEN col_cur;
    alter_loop: LOOP
      FETCH col_cur INTO v_table, v_col, v_nullable, v_extra;
      IF v_done THEN LEAVE alter_loop; END IF;
      SET @s = CONCAT(
        'ALTER TABLE `', v_table, '` MODIFY `', v_col, '` INT UNSIGNED ',
        IF(v_nullable = 'NO', 'NOT NULL', 'NULL'),
        IF(v_extra IS NOT NULL AND v_extra <> '', CONCAT(' ', v_extra), '')
      );
      PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE col_cur;
  END;

  -- 3) Recreate every FK we dropped, reusing the original constraint names.
  BEGIN
    DECLARE v_done       INT DEFAULT 0;
    DECLARE v_table      VARCHAR(128);
    DECLARE v_constraint VARCHAR(128);
    DECLARE v_col        VARCHAR(128);
    DECLARE v_ref_table  VARCHAR(128);
    DECLARE v_ref_col    VARCHAR(128);
    DECLARE recr_cur CURSOR FOR
      SELECT table_name, constraint_name, column_name, ref_table, ref_column
        FROM _fk_snapshot;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    OPEN recr_cur;
    recr_loop: LOOP
      FETCH recr_cur INTO v_table, v_constraint, v_col, v_ref_table, v_ref_col;
      IF v_done THEN LEAVE recr_loop; END IF;
      SET @s = CONCAT(
        'ALTER TABLE `', v_table,
        '` ADD CONSTRAINT `', v_constraint,
        '` FOREIGN KEY (`', v_col,
        '`) REFERENCES `', v_ref_table, '`(`', v_ref_col, '`)'
      );
      PREPARE stmt FROM @s; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE recr_cur;
  END;

  DROP TEMPORARY TABLE IF EXISTS _fk_snapshot;
END$$

DELIMITER ;

SET @OLD_FK := @@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS = 0;
CALL prg_make_ids_unsigned();
SET FOREIGN_KEY_CHECKS = @OLD_FK;
DROP PROCEDURE prg_make_ids_unsigned;

-- Alinea la base existente con las restricciones que el código declara al crear las tablas.
-- Idempotente: se puede ejecutar varias veces y no duplica restricciones ya presentes.
-- Requisito previo: los datos actuales deben cumplirlas (verificado en Neon el 2026-10-28).
BEGIN;

DO $$
BEGIN
    -- detections.confianza entre 0 y 1 (en el código: CHECK(confianza BETWEEN 0 AND 1)).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'detections'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%confianza%'
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT chk_detection_confianza CHECK (confianza >= 0 AND confianza <= 1);
    END IF;

    -- detections.timestamp no negativo (en el código: CHECK(timestamp >= 0)).
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'detections'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%"timestamp"%'
    ) THEN
        ALTER TABLE detections
            ADD CONSTRAINT chk_detection_timestamp CHECK ("timestamp" >= 0);
    END IF;

    -- Una alerta por detección de apertura (en el código: UNIQUE(detection_id)).
    -- INSERT OR IGNORE en alerts depende de esta unicidad.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = 'alerts'::regclass AND i.indisunique
          AND i.indnatts = 1 AND a.attname = 'detection_id'
    ) THEN
        ALTER TABLE alerts ADD CONSTRAINT uq_alert_detection UNIQUE (detection_id);
    END IF;
END $$;

COMMIT;

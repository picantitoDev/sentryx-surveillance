-- Alinear Neon con los campos opcionales del backend; conserva los registros.
BEGIN;
ALTER TABLE public.inference_logs ALTER COLUMN camera DROP NOT NULL;
ALTER TABLE public.detections ALTER COLUMN session_id DROP NOT NULL;
COMMIT;

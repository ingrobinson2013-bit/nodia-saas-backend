-- migrations/create_voice_transcriptions.sql
-- Tabla para registrar trazabilidad de notas de voz transcribidas con Whisper

CREATE TABLE IF NOT EXISTS voice_transcriptions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    phone TEXT NOT NULL,
    media_id TEXT NOT NULL,
    transcript TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índices de búsqueda para auditoría
CREATE INDEX IF NOT EXISTS idx_voice_transcriptions_phone ON voice_transcriptions(phone);
CREATE INDEX IF NOT EXISTS idx_voice_transcriptions_created ON voice_transcriptions(created_at DESC);

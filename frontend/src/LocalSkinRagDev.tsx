
import { useState } from "react";

const LOCAL_PREVIEW_URL =
  "http://127.0.0.1:8000/api/v1/medicine/dev-preview";

export default function LocalSkinRagDev() {
  const [isOpen, setIsOpen] = useState(false);

  // Never show developer tools in production builds.
  if (!import.meta.env.DEV) {
    return null;
  }

  return (
    <section
      style={{
        marginTop: 24,
        padding: 20,
        borderRadius: 14,
        border: "1px solid #475569",
        background: "#172033",
        color: "#f8fafc",
      }}
    >
      <h3>Local Skin Analysis + Medicine RAG</h3>

      <p style={{ color: "#cbd5e1" }}>
        Developer-only testing using local Ollama
        and unreviewed medicine references.
      </p>

      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        style={{
          background: "#2563eb",
          color: "white",
          border: 0,
          borderRadius: 8,
          padding: "12px 18px",
          cursor: "pointer",
          fontWeight: 600,
        }}
      >
        {isOpen
          ? "Close Local Skin RAG"
          : "Open Local Skin RAG"}
      </button>

      {isOpen && (
        <div style={{ marginTop: 18 }}>
          <p style={{ fontSize: 13, color: "#fbbf24" }}>
            Experimental developer preview.
            Not for medical diagnosis or treatment.
          </p>

          <iframe
            src={LOCAL_PREVIEW_URL}
            title="Mateo Local Skin RAG Developer Preview"
            style={{
              width: "100%",
              height: "850px",
              border: "1px solid #475569",
              borderRadius: 12,
              background: "#101827",
            }}
          />

          <p style={{ fontSize: 13 }}>
            If the preview does not load inside Mateo,{" "}
            <a
              href={LOCAL_PREVIEW_URL}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#93c5fd" }}
            >
              open it in a separate tab
            </a>.
          </p>
        </div>
      )}
    </section>
  );
}

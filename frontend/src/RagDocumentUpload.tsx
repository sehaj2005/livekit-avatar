import React, {
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { meetingApiUrl } from "./meetingUrls";

type Props = {
  accessToken: string;
  meetingId?: string;
  onUploaded?: () => void;
  disabled?: boolean;
};

export default function RagDocumentUpload({
  accessToken,
  meetingId,
  onUploaded,
  disabled = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  async function uploadDocument(
    event: ChangeEvent<HTMLInputElement>
  ) {
    const file = event.target.files?.[0];

    if (!file) return;

    setError("");
    setStatus("");

    if (!accessToken) {
      setError("Connect to the meeting or sign in as an admin to upload documents.");
      event.target.value = "";
      return;
    }

    if (file.size > 10 * 1024 * 1024) {
      setError("Maximum document size is 10 MB.");
      event.target.value = "";
      return;
    }

    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("file", file);
      if (meetingId) formData.append("meeting_id", meetingId);

      const response = await fetch(
        `${meetingApiUrl}/api/v1/${meetingId ? "skin/documents" : "medicine/upload"}`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
          },
          body: formData,
        }
      );

      const result = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : "Document upload failed."
        );
      }

      setStatus(
        `${result.original_filename} uploaded and indexed. ` +
        `Review status: ${result.review_status}.`
      );
      onUploaded?.();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Unable to upload document."
      );
    } finally {
      setUploading(false);

      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept=".txt,.md,.pdf"
        style={{ display: "none" }}
        onChange={(event) => void uploadDocument(event)}
      />

      <button
        type="button"
        disabled={disabled || uploading}
        onClick={() => inputRef.current?.click()}
      >
        {uploading ? "Indexing Document..." : "Upload RAG Documents"}
      </button>

      {status && (
        <p role="status" style={{ color: "#86efac" }}>
          {status}
        </p>
      )}

      {error && (
        <p role="alert" style={{ color: "#fca5a5" }}>
          {error}
        </p>
      )}
    </div>
  );
}

import { useRef, useState } from "react";

import { getArtifactContent } from "../../api/records";
import type { Artifact } from "../../api/records";

interface ArtifactPreviewProps {
  artifact: Artifact;
  title?: string;
  loadContent?: (artifactId: string) => Promise<string>;
}

const TEXT_MEDIA_TYPES = new Set([
  "application/json",
  "application/x-ndjson",
]);

function supportsInlinePreview(mediaType: string): boolean {
  const normalized = mediaType.split(";", 1)[0].trim().toLowerCase();
  return normalized.startsWith("text/") || TEXT_MEDIA_TYPES.has(normalized);
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "读取产物失败，请重试。";
}

export function ArtifactPreview({
  artifact,
  title = artifact.kind,
  loadContent = getArtifactContent,
}: ArtifactPreviewProps) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const inFlight = useRef<Promise<void> | null>(null);
  const supported = supportsInlinePreview(artifact.media_type);

  const load = () => {
    if (!supported || content !== null || inFlight.current) return;
    setLoading(true);
    setError(null);
    const request = loadContent(artifact.id)
      .then((value) => setContent(value))
      .catch((reason: unknown) => setError(errorMessage(reason)))
      .finally(() => {
        setLoading(false);
        inFlight.current = null;
      });
    inFlight.current = request;
  };

  return (
    <div className="artifact-preview" data-testid={`bound-artifact-${artifact.id}`}>
      <details
        className="timeline-entry__artifact-disclosure"
        data-testid={`artifact-preview-${artifact.id}`}
        onToggle={(event) => {
          if (event.currentTarget.open) load();
        }}
      >
        <summary className="timeline-entry__summary">
          <span className="timeline-entry__badge timeline-entry__badge--artifact">产物</span>
          <span className="timeline-entry__title">
            <strong>{title}</strong>
            <small>
              {artifact.media_type} · {artifact.size.toLocaleString()} bytes
            </small>
          </span>
        </summary>
        <div className="timeline-entry__details artifact-preview__details" aria-busy={loading}>
          <code>sha256: {artifact.sha256}</code>
          {!supported ? <p>此文件格式暂不支持页面内查看。</p> : null}
          {loading ? <p>正在读取完整内容…</p> : null}
          {error ? (
            <div className="artifact-preview__error" role="alert">
              <p>{error}</p>
              <button type="button" onClick={load}>重试查看</button>
            </div>
          ) : null}
          {content !== null ? (
            <pre
              className="artifact-preview__content"
              data-testid={`artifact-content-${artifact.id}`}
            >
              {content}
            </pre>
          ) : null}
          <a href={`/api/v1/artifacts/${artifact.id}`}>下载原文件</a>
        </div>
      </details>
    </div>
  );
}

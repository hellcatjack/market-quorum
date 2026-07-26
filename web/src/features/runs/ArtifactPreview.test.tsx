import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { Artifact } from "../../api/records";
import { ArtifactPreview } from "./ArtifactPreview";

function artifact(mediaType = "text/markdown"): Artifact {
  return {
    id: "artifact-report",
    run_id: "run-123",
    kind: "report_18_complete_report",
    media_type: mediaType,
    size: 106_886,
    sha256: "report-sha",
    created_at: "2026-07-26T00:42:37Z",
  };
}

function toggle(details: HTMLElement, open: boolean) {
  if ((details as HTMLDetailsElement).open !== open) {
    const summary = details.querySelector("summary");
    if (!summary) throw new Error("artifact preview summary is missing");
    fireEvent.click(summary);
  }
}

test("loads a full text artifact only when first expanded and renders it as inert text", async () => {
  const loadContent = vi
    .fn()
    .mockResolvedValue("# 完整报告\n<script>alert('never')</script>\n最后一行");
  const { container } = render(
    <ArtifactPreview artifact={artifact()} loadContent={loadContent} />,
  );
  const disclosure = screen.getByTestId("artifact-preview-artifact-report");

  expect(disclosure).not.toHaveAttribute("open");
  expect(loadContent).not.toHaveBeenCalled();

  toggle(disclosure, true);

  const content = await screen.findByTestId("artifact-content-artifact-report");
  expect(loadContent).toHaveBeenCalledTimes(1);
  expect(loadContent).toHaveBeenCalledWith("artifact-report");
  expect(content).toHaveTextContent("最后一行");
  expect(content).toHaveTextContent("<script>alert('never')</script>");
  expect(container.querySelector("script")).toBeNull();

  toggle(disclosure, false);
  toggle(disclosure, true);
  await waitFor(() => expect(loadContent).toHaveBeenCalledTimes(1));
});

test("shows a local error and retries the artifact request", async () => {
  const loadContent = vi
    .fn()
    .mockRejectedValueOnce(new Error("读取失败"))
    .mockResolvedValueOnce("重试后的完整正文");
  render(<ArtifactPreview artifact={artifact()} loadContent={loadContent} />);

  toggle(screen.getByTestId("artifact-preview-artifact-report"), true);

  expect(await screen.findByRole("alert")).toHaveTextContent("读取失败");
  fireEvent.click(screen.getByRole("button", { name: "重试查看" }));

  expect(await screen.findByText("重试后的完整正文")).toBeInTheDocument();
  expect(loadContent).toHaveBeenCalledTimes(2);
});

test("does not request unsupported binary content", async () => {
  const loadContent = vi.fn();
  render(
    <ArtifactPreview
      artifact={artifact("application/octet-stream")}
      loadContent={loadContent}
    />,
  );

  toggle(screen.getByTestId("artifact-preview-artifact-report"), true);

  expect(await screen.findByText("此文件格式暂不支持页面内查看。")).toBeInTheDocument();
  expect(loadContent).not.toHaveBeenCalled();
});

test("accepts textual media types that include charset parameters", async () => {
  const loadContent = vi.fn().mockResolvedValue('{"status":"ok"}');
  render(
    <ArtifactPreview
      artifact={artifact("application/json; charset=utf-8")}
      loadContent={loadContent}
    />,
  );

  toggle(screen.getByTestId("artifact-preview-artifact-report"), true);

  expect(await screen.findByText('{"status":"ok"}')).toBeInTheDocument();
  expect(loadContent).toHaveBeenCalledTimes(1);
});

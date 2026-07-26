import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import type { Comment, Review } from "../../api/records";
import { createComment, createReview } from "../../api/records";
import { LocalTime } from "./RunTimeline";

export function ReviewPanel({ runId, reviews, comments, canReview }: { runId: string; reviews: Review[]; comments: Comment[]; canReview: boolean }) {
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const [reviewComment, setReviewComment] = useState("");
  const [verdict, setVerdict] = useState("approved");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["run-collaboration", runId] });
  const commentMutation = useMutation({ mutationFn: () => createComment(runId, body), onSuccess: () => { setBody(""); void refresh(); } });
  const reviewMutation = useMutation({ mutationFn: () => createReview(runId, verdict, reviewComment), onSuccess: () => { setReviewComment(""); void refresh(); } });
  return (
    <section className="detail-panel detail-panel--wide">
      <div className="section-heading"><p className="eyebrow">团队协作</p><h2>复核与评论</h2></div>
      <div className="collaboration-grid">
        <div>
          <h3>复核记录</h3>
          {reviews.map((review) => <article className="collaboration-entry" key={review.id}><strong>{review.verdict} · {review.reviewer}</strong><p>{review.comment}</p><LocalTime value={review.created_at} /></article>)}
          {canReview ? <form onSubmit={(event) => { event.preventDefault(); reviewMutation.mutate(); }}><select aria-label="复核结论" value={verdict} onChange={(event) => setVerdict(event.target.value)}><option value="approved">通过</option><option value="changes_requested">要求修改</option><option value="rejected">拒绝</option></select><textarea aria-label="复核意见" required value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} /><button type="submit" disabled={reviewMutation.isPending}>提交复核</button></form> : null}
        </div>
        <div>
          <h3>讨论</h3>
          {comments.map((comment) => <article className="collaboration-entry" key={comment.id}><strong>{comment.author}</strong><p>{comment.body}</p><LocalTime value={comment.created_at} /></article>)}
          <form onSubmit={(event) => { event.preventDefault(); commentMutation.mutate(); }}><textarea aria-label="添加评论" required value={body} onChange={(event) => setBody(event.target.value)} /><button type="submit" disabled={commentMutation.isPending}>发表评论</button></form>
        </div>
      </div>
    </section>
  );
}

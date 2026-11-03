import { useEffect, useRef, useState } from "react";
import type { AccountUsage, InteractionUsage, TokenUsage } from "./types";

export function UsageDetails({
  usage,
}: {
  usage: TokenUsage & Partial<InteractionUsage>;
}) {
  return (
    <details className="usage-details">
      <summary>
        本次 {usage.total_tokens.toLocaleString()} tokens
        {usage.unknown_calls > 0 && " + 未知用量"}
        {usage.session_total !== undefined &&
          ` · 会话累计 ${usage.session_total.toLocaleString()}`}
      </summary>
      <div>
        输入 {usage.input_tokens.toLocaleString()} · 输出{" "}
        {usage.output_tokens.toLocaleString()}
      </div>
      {Object.entries(usage.agents).map(([agent, value]) => (
        <div key={agent}>
          {agent}: {value.total_tokens.toLocaleString()}
          {value.unknown_calls > 0 && `（${value.unknown_calls} 次用量未知）`}
        </div>
      ))}
    </details>
  );
}

export function TokenBalance({
  usage,
  animate,
}: {
  usage: AccountUsage;
  animate: boolean;
}) {
  const [consumed, setConsumed] = useState(usage.total_tokens);
  const previous = useRef(usage.total_tokens);
  useEffect(() => {
    const from = previous.current;
    previous.current = usage.total_tokens;
    if (
      !animate ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setConsumed(usage.total_tokens);
      return;
    }
    const start = performance.now();
    let frame = 0;
    function tick(now: number) {
      const progress = Math.min(1, (now - start) / 400);
      setConsumed(Math.round(from + (usage.total_tokens - from) * progress));
      if (progress < 1) frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [usage.total_tokens, animate]);
  return (
    <details className="token-balance">
      <summary>
        累计消耗 {consumed.toLocaleString()} tokens
        {usage.unknown_calls > 0 && " *"}
      </summary>
      <div className="token-popover">
        <b>用户累计消耗</b>
        <p>已统计 {usage.total_tokens.toLocaleString()} tokens</p>
        <p>
          输入 {usage.input_tokens.toLocaleString()} · 输出{" "}
          {usage.output_tokens.toLocaleString()}
        </p>
        {Object.entries(usage.agents).map(([agent, value]) => (
          <p key={agent}>
            {agent}: {value.total_tokens.toLocaleString()}
          </p>
        ))}
        <small>
          自 {new Date(usage.started_at).toLocaleDateString()}{" "}
          启用统计；不含此前调用。
          {usage.unknown_calls > 0 &&
            `${usage.unknown_calls} 次调用用量未知，实际消耗可能更高。`}
          统计仅包含服务收到的模型用量，不代表平台账单。
        </small>
      </div>
    </details>
  );
}

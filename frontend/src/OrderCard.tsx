import {
  Check,
  ChevronDown,
  Clock3,
  MapPin,
  RefreshCw,
  ShoppingBag,
  X,
} from "lucide-react";
import { money, type OrderCardData } from "./types";

type Props = {
  card: OrderCardData;
  active?: boolean;
  busy?: boolean;
  expanded?: boolean;
  onDecide?: (id: string, action: "confirm" | "cancel") => void;
  onRefresh?: () => void;
};
const time = (value: string) =>
  new Date(value).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });

export default function OrderCard({
  card,
  active = false,
  busy = false,
  expanded = false,
  onDecide,
  onRefresh,
}: Props) {
  const { cart, approval, order_result: result } = card;
  const pending =
    result?.outcome === "pending" || result?.outcome === "unknown";
  const label =
    approval.status === "cancelled"
      ? "已取消"
      : result?.outcome === "succeeded"
        ? "已下单 · 模拟支付成功"
        : result?.outcome === "rejected"
          ? "下单失败"
          : pending
            ? "结果待确认"
            : approval.status === "pending"
              ? "等待你的确认"
              : "已处理";
  return (
    <article className="order-card" aria-label="订单卡片">
      <div className="order-heading">
        <span className="order-icon">
          <ShoppingBag size={17} />
        </span>
        <div>
          <span className="eyebrow">YOUR ORDER</span>
          <h3>{cart.restaurant.name}</h3>
        </div>
        <span className={`order-state ${result?.outcome || approval.status}`}>
          {label}
        </span>
      </div>
      <div className="order-summary">
        <div>
          {cart.items.map((item, i) => (
            <p key={i}>
              {item.name}
              <span>× {item.quantity}</span>
            </p>
          ))}
        </div>
        <strong>{money(cart.amounts.total)}</strong>
      </div>
      <details open={expanded || undefined}>
        <summary>
          查看订单详情
          <ChevronDown size={14} />
        </summary>
        <div className="order-details">
          <div className="item-details">
            {cart.items.map((item, i) => (
              <div key={i}>
                <b>
                  {item.name} × {item.quantity}
                </b>
                <span>{money(item.unit_price * item.quantity)}</span>
                <p>{item.selected_option_names.join(" · ") || "标准份"}</p>
              </div>
            ))}
          </div>
          <dl className="amounts">
            <div>
              <dt>商品金额</dt>
              <dd>{money(cart.amounts.subtotal)}</dd>
            </div>
            <div>
              <dt>包装费</dt>
              <dd>{money(cart.amounts.packaging_fee)}</dd>
            </div>
            <div>
              <dt>配送费</dt>
              <dd>{money(cart.amounts.delivery_fee)}</dd>
            </div>
            {cart.amounts.discount > 0 && (
              <div>
                <dt>优惠</dt>
                <dd>−{money(cart.amounts.discount)}</dd>
              </div>
            )}
            <div className="amount-total">
              <dt>合计</dt>
              <dd>{money(cart.amounts.total)}</dd>
            </div>
          </dl>
          <div className="delivery">
            <MapPin size={15} />
            <div>
              <p>{cart.delivery_address.address}</p>
              <span>
                {cart.delivery_address.contact} · {cart.delivery_address.phone}
              </span>
            </div>
          </div>
          <p className="arrival">
            <Clock3 size={14} /> 预计 {time(cart.estimated_arrival[0])}–
            {time(cart.estimated_arrival[1])} 送达
          </p>
          {!!card.dietary_preferences?.length && (
            <div className="memory-basis">
              <b>本轮推荐参考的饮食偏好</b>
              {card.dietary_preferences.map((p, i) => (
                <p key={i}>
                  {p.subject}：{p.polarity === "dislike" ? "不喜欢" : ""}
                  {p.value}
                  <span>
                    {p.source === "explicit"
                      ? "你明确表达"
                      : `${p.evidence_count} 笔订单推断`}
                  </span>
                </p>
              ))}
              <small>只用于推荐；本次规格以你选择的订单明细为准。</small>
            </div>
          )}
          {result?.order && (
            <p className="reference">订单号：{result.order.order_id}</p>
          )}
        </div>
      </details>
      {result?.message && <p className="result-message">{result.message}</p>}
      {active && approval.status === "pending" && (
        <div className="approval-actions">
          <p>请核对详情，确认后提交订单并模拟支付。</p>
          <div>
            <button
              className="primary"
              disabled={busy}
              onClick={() => onDecide?.(approval.id, "confirm")}
            >
              <Check size={15} />
              确认下单 · {money(cart.amounts.total)}
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => onDecide?.(approval.id, "cancel")}
            >
              <X size={14} />
              取消
            </button>
          </div>
        </div>
      )}
      {active && pending && (
        <div className="pending-actions">
          <p>结果尚未确认，请勿重复下单。</p>
          <button className="secondary" disabled={busy} onClick={onRefresh}>
            <RefreshCw size={14} />
            查询订单状态
          </button>
          <small>请求编号：{card.request_reference}</small>
        </div>
      )}
    </article>
  );
}

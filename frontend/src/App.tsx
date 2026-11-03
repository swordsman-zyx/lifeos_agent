import { useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ChevronRight,
  LoaderCircle,
  Menu,
  MessageSquare,
  Plus,
  RefreshCw,
  ShoppingBag,
  Sparkles,
  X,
} from "lucide-react";
import OrderCard from "./OrderCard";
import { TokenBalance, UsageDetails } from "./TokenUsage";
import type { AccountUsage } from "./types";
import {
  money,
  type Session,
  type View,
  type Candidate,
  type ChatBody,
  type RecentOrder,
} from "./types";

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    "/api" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const data = await response.json();
    throw new Error(
      typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试",
    );
  }
  return response.json();
}

export default function App() {
  const [account, setAccount] = useState<AccountUsage | null>(null);
  const [animateUsage, setAnimateUsage] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [orders, setOrders] = useState<RecentOrder[]>([]);
  const [view, setView] = useState<View | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<ChatBody | null>(null);
  const [selectedOrder, setSelectedOrder] = useState<RecentOrder | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const inFlight = useRef(false);
  const scroll = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const activeSession = useRef<string | null>(null);

  function adopt(data: View, animate = false) {
    if (data.usage) setAccount(data.usage.account);
    setAnimateUsage(animate);
    setView(data);
    activeSession.current = data.session_id;
    localStorage.setItem("lifeos-session", data.session_id);
  }
  async function run(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "暂时无法连接服务");
      try {
        setAccount(await api<AccountUsage>("/usage"));
        setAnimateUsage(true);
      } catch {
        /* Keep the last known balance while offline. */
      }
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }
  async function reloadLists() {
    const [s, o] = await Promise.all([
      api<{ sessions: Session[] }>("/sessions"),
      api<{ orders: RecentOrder[] }>("/orders?limit=10"),
    ]);
    setSessions(s.sessions);
    setOrders(o.orders);
  }
  async function reconnect() {
    await run(async () => {
      await api("/health");
      const data = await api<{ sessions: Session[] }>("/sessions");
      const id =
        activeSession.current || localStorage.getItem("lifeos-session");
      const target = data.sessions.find((s) => s.id === id) || data.sessions[0];
      adopt(
        target
          ? await api<View>("/sessions/" + target.id)
          : await api<View>("/sessions", {}),
      );
      await reloadLists();
    });
  }
  useEffect(() => {
    void reconnect();
  }, []);
  useEffect(() => {
    if (!selectedOrder)
      end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [view, busy, selectedOrder]);
  const blocked =
    busy ||
    view?.status === "awaiting_confirmation" ||
    view?.status === "order_pending";

  async function send(
    message = text,
    candidate?: Candidate,
    original?: ChatBody,
  ) {
    if (!view || !message.trim() || blocked) return;
    const body = original || {
      session_id: view.session_id,
      client_message_id: crypto.randomUUID(),
      message: message.trim(),
      ...(candidate
        ? { selection: { kind: candidate.kind, id: candidate.id } }
        : {}),
    };
    await run(async () => {
      setSelectedOrder(null);
      setRetry(body);
      adopt(await api<View>("/lifeos/chat", body), true);
      setRetry(null);
      setText("");
      await reloadLists();
    });
  }
  async function decide(id: string, action: "confirm" | "cancel") {
    if (!view) return;
    await run(async () => {
      adopt(
        await api<View>("/lifeos/resume", {
          session_id: view.session_id,
          approval_id: id,
          action,
        }),
        true,
      );
      await reloadLists();
    });
  }
  async function refreshOrder() {
    if (!view) return;
    await run(async () => {
      adopt(
        await api<View>(
          `/lifeos/sessions/${view.session_id}/order/refresh`,
          {},
        ),
        true,
      );
      await reloadLists();
    });
  }
  async function switchSession(id: string) {
    await run(async () => {
      adopt(await api<View>("/sessions/" + id));
      setSelectedOrder(null);
      setRetry(null);
      setText("");
      setSidebarOpen(false);
      await reloadLists();
    });
  }
  const waiting = view?.status === "awaiting_confirmation";
  const latestPending = view?.status === "order_pending";
  return (
    <div className="app-shell">
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭导航"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <a className="brand" href="/" aria-label="LifeOS 首页">
          <span className="brand-icon">
            <Sparkles size={21} />
          </span>
          <div>
            LifeOS<span>你的生活服务助手</span>
          </div>
        </a>
        <button
          className="new-session"
          disabled={busy}
          onClick={() =>
            run(async () => {
              adopt(await api<View>("/sessions", {}));
              setSelectedOrder(null);
              setRetry(null);
              setText("");
              setSidebarOpen(false);
              await reloadLists();
            })
          }
        >
          <Plus size={16} />
          开启新会话
        </button>
        <div className="sidebar-sections">
          <section className="sidebar-section">
            <h2>
              最近会话<span>{sessions.length}</span>
            </h2>
            <nav aria-label="最近会话">
              {sessions.map((s) => (
                <button
                  key={s.id}
                  disabled={busy}
                  title={s.title}
                  aria-label={s.title}
                  className={`session ${s.id === view?.session_id ? "selected" : ""}`}
                  onClick={() => switchSession(s.id)}
                >
                  <MessageSquare size={14} />
                  <span>{s.title}</span>
                </button>
              ))}
              {!sessions.length && (
                <p className="list-empty">从一段新对话开始</p>
              )}
            </nav>
          </section>
          <section className="sidebar-section orders-section">
            <h2>
              最近订单<span>{orders.length}</span>
            </h2>
            <p className="orders-caption">已下单 · 模拟支付成功</p>
            <nav aria-label="最近订单">
              {orders.map((row) => (
                <button
                  key={row.order.order_id}
                  disabled={busy}
                  className={`recent-order ${selectedOrder?.order.order_id === row.order.order_id ? "selected" : ""}`}
                  onClick={() => {
                    setSelectedOrder(row);
                    setSidebarOpen(false);
                    scroll.current?.scrollTo({ top: 0, behavior: "smooth" });
                  }}
                >
                  <span className="small-order-icon">
                    <ShoppingBag size={14} />
                  </span>
                  <div>
                    <b>{row.order.cart.restaurant.name}</b>
                    <span>
                      {new Date(row.order.created_at).toLocaleDateString(
                        "zh-CN",
                        { month: "2-digit", day: "2-digit" },
                      )}{" "}
                      · {money(row.order.cart.amounts.total)}
                    </span>
                  </div>
                  <ChevronRight size={12} />
                </button>
              ))}
              {!orders.length && (
                <p className="list-empty">完成的订单会收在这里</p>
              )}
            </nav>
          </section>
        </div>
        <div className="sidebar-foot">
          <span className="online-dot" />
          <div>LifeOS 已就绪</div>
          <span className="version">v0.3</span>
        </div>
      </aside>
      <main
        className={
          !view?.messages.length && !selectedOrder ? "empty-session" : ""
        }
      >
        <header className="topbar">
          <div className="header-title">
            <button
              className="mobile-menu icon-button"
              aria-label="打开导航"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={19} />
            </button>
            <span className="header-mark">
              <Sparkles size={16} />
            </span>
            <div>
              <b>LifeOS 助手</b>
            </div>
          </div>
          <div className="header-actions">
            <a
              className="debug-link"
              href="https://smith.langchain.com"
              target="_blank"
              rel="noreferrer"
            >
              LangSmith
            </a>
            {account && <TokenBalance usage={account} animate={animateUsage} />}
            <button
              className="icon-button"
              disabled={busy}
              onClick={reconnect}
              aria-label="同步会话"
              title="同步会话"
            >
              <RefreshCw size={16} />
            </button>
            <span className="avatar">你</span>
          </div>
        </header>
        <div className="chat-scroll" ref={scroll}>
          <div className="conversation">
            {selectedOrder ? (
              <section className="history-detail">
                <div className="history-heading">
                  <div>
                    <span className="eyebrow">ORDER HISTORY</span>
                    <h1>订单详情</h1>
                    <p>历史订单只读，不会再次提交。</p>
                  </div>
                  <button
                    className="secondary"
                    onClick={() => setSelectedOrder(null)}
                  >
                    <X size={14} />
                    返回会话
                  </button>
                </div>
                <OrderCard
                  key={selectedOrder.order.order_id}
                  expanded
                  card={{
                    id: selectedOrder.order.order_id,
                    cart: selectedOrder.order.cart,
                    approval: { id: "", status: "confirmed" },
                    order_result: {
                      outcome: "succeeded",
                      order: selectedOrder.order,
                    },
                  }}
                />
              </section>
            ) : (
              <>
                {!view?.messages.length && (
                  <section className="welcome">
                    <h1>你有什么吩咐？</h1>
                  </section>
                )}
                {view?.messages.map((m) => (
                  <div className={`message ${m.role}`} key={m.id}>
                    {m.role === "assistant" && (
                      <span className="bot-icon">
                        <Sparkles size={15} />
                      </span>
                    )}
                    <div className="message-body">
                      <span className="message-label">
                        {m.role === "assistant" ? "LIFEOS" : "你"}
                      </span>
                      <div className="bubble">{m.content}</div>
                      {m.usage && <UsageDetails usage={m.usage} />}
                      {m.cards?.map((card) => (
                        <OrderCard
                          key={card.id}
                          card={card}
                          busy={busy}
                          active={
                            view.approval?.id === card.id &&
                            (waiting || latestPending)
                          }
                          onDecide={decide}
                          onRefresh={refreshOrder}
                        />
                      ))}
                    </div>
                  </div>
                ))}
                {!!view?.candidates?.length && !blocked && (
                  <div className="candidates">
                    {(view.candidates ?? []).map((c, i) => (
                      <button
                        key={c.kind + c.id}
                        disabled={busy}
                        onClick={() => send(`选择${c.name}`, c)}
                      >
                        <span className="candidate-index">0{i + 1}</span>
                        <div>
                          <b>{c.name}</b>
                          <p>{c.description}</p>
                        </div>
                        <span className="candidate-price">
                          {c.price !== undefined ? money(c.price) : "选择"}
                          <ChevronRight size={13} />
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
            {busy && (
              <div className="thinking" role="status">
                <LoaderCircle className="spin" size={14} />
                正在为你处理…
              </div>
            )}
            <div ref={end} />
          </div>
        </div>
        <div className="composer-dock">
          <div className="composer-inner">
            {error && (
              <div className="error" role="alert">
                <span>{error}</span>
                {retry && retry.session_id === view?.session_id ? (
                  <button
                    disabled={busy}
                    onClick={() => send(retry.message, undefined, retry)}
                  >
                    重试原请求
                  </button>
                ) : (
                  <button disabled={busy} onClick={reconnect}>
                    重新连接
                  </button>
                )}
              </div>
            )}
            {waiting && (
              <div className="input-status">
                <ShoppingBag size={13} />
                订单等你确认，请在会话中的订单卡片操作。
              </div>
            )}
            {latestPending && (
              <div className="input-status">
                <RefreshCw size={13} />
                当前订单结果待确认，请在订单卡片继续查询。
              </div>
            )}
            <div className="composer">
              <textarea
                aria-label="生活服务需求"
                value={text}
                onChange={(e) => setText(e.target.value)}
                disabled={blocked || !view}
                placeholder={
                  blocked ? "请先完成当前订单操作" : "向 LifeOS 发送消息"
                }
                rows={2}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <button
                aria-label="发送消息"
                disabled={blocked || !view || !text.trim()}
                onClick={() => send()}
              >
                <ArrowUp size={19} />
              </button>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
            <p className="composer-note">暂时支持点外卖</p>
          </div>
        </div>
      </main>
    </div>
  );
}

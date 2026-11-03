export type Session = { id: string; title: string; updated_at: string };
export type Preference = {
  dimension: string;
  subject: string;
  value: string;
  polarity: string;
  source: "explicit" | "inferred";
  evidence_count: number;
  source_ids: string[];
};
export type Cart = {
  cart_id: string;
  cart_version: number;
  restaurant: { name: string };
  delivery_address: { address: string; contact: string; phone: string };
  items: {
    name: string;
    quantity: number;
    selected_option_names: string[];
    unit_price: number;
  }[];
  amounts: {
    subtotal: number;
    packaging_fee: number;
    delivery_fee: number;
    discount: number;
    total: number;
  };
  estimated_arrival: string[];
};
export type Order = {
  order_id: string;
  created_at: string;
  payment_status: string;
  cart: Cart;
};
export type OrderResult = { outcome: string; order?: Order; message?: string };
export type OrderCardData = {
  id: string;
  cart: Cart;
  approval: { id: string; status: string };
  order_result?: OrderResult | null;
  request_reference?: string;
  dietary_preferences?: Preference[];
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  cards?: OrderCardData[];
  usage?: InteractionUsage;
};
export type Candidate = {
  kind: "food" | "restaurant" | "address";
  id: string;
  name: string;
  description: string;
  price?: number;
};
export type View = {
  usage?: { account: AccountUsage; session: TokenUsage };
  session_id: string;
  session: Session;
  status: string;
  messages: Message[];
  candidates: Candidate[];
  approval: { id: string; status: string } | null;
  dietary_preferences?: Preference[];
};
export type RecentOrder = { session_id: string; order: Order };
export type ChatBody = {
  session_id: string;
  client_message_id: string;
  message: string;
  selection?: { kind: string; id: string };
};
export const money = (amount: number) => `¥${(amount / 100).toFixed(2)}`;

export type TokenUsage = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  unknown_calls: number;
  calls: number;
  agents: Record<string, { total_tokens: number; unknown_calls: number }>;
};
export type InteractionUsage = TokenUsage & { session_total: number };
export type AccountUsage = TokenUsage & {
  started_at: string;
};

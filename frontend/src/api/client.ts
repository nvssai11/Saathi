export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseErrorBody(res: Response): Promise<{ message: string; code?: string }> {
  try {
    const body = await res.json();
    const detail = body.error ?? body.detail ?? body;
    if (typeof detail === "string") return { message: detail };
    if (detail && typeof detail === "object") {
      return { message: detail.message ?? res.statusText, code: detail.code };
    }
    return { message: res.statusText };
  } catch {
    return { message: res.statusText };
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string } = {}
): Promise<T> {
  const { token, headers, ...rest } = options;
  const res = await fetch(path, {
    ...rest,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });

  if (!res.ok) {
    const { message, code } = await parseErrorBody(res);
    throw new ApiError(res.status, message, code);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function jsonBody(payload: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function uploadWithProgress<T>(
  path: string,
  form: FormData,
  token: string,
  onProgress?: (pct: number) => void,
  timeoutMs = 25000
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.timeout = timeoutMs;

    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve((xhr.responseText ? JSON.parse(xhr.responseText) : undefined) as T);
        return;
      }
      let message = xhr.statusText || "Request failed";
      let code: string | undefined;
      try {
        const body = JSON.parse(xhr.responseText);
        const detail = body.error ?? body.detail ?? body;
        if (typeof detail === "string") message = detail;
        else if (detail && typeof detail === "object") {
          message = detail.message ?? message;
          code = detail.code;
        }
      } catch {}
      reject(new ApiError(xhr.status, message, code));
    };

    xhr.onerror = () => reject(new ApiError(0, "Network error — check your connection and try again."));
    xhr.ontimeout = () => reject(new ApiError(0, "Upload timed out — the connection may be too slow."));
    xhr.send(form);
  });
}

export interface PlaceOrderRequest {
  buyer_ref: string;
  product_type: string;
  total_qty: number;
  quality_min: number;
  deadline: string;
}

export interface PlaceOrderResponse {
  order_id: number;
  correlation_id: string;
  status: string;
}

export interface OrderListItem {
  order_id: number;
  status: string;
  product_type: string;
  total_qty: number;
  deadline: string;
  created_at: string;
}

export interface OrderListResponse {
  orders: OrderListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface OrderStatusResponse {
  order_id: number;
  correlation_id: string;
  status: string;
  total_qty: number;
  sublots_total: number;
  sublots_delivered: number;
  sublots_verified: number;
  sublots_failed: number;
}

export interface QuoteLineItem {
  product_type: string;
  total_qty: number;
  unit_price: string;
  subtotal: string;
}

export interface OrderQuoteResponse {
  order_id: number;
  line_items: QuoteLineItem[];
  platform_fee: string;
  total: string;
}

export interface SettlementSummaryResponse {
  order_id: number;
  buyer_base: string;
  platform_fee: string;
  buyer_total: string;
}

export interface WorkshopCapacityUpdateRequest {
  product_type: string;
  available_qty: number;
  cost_per_unit: number;
  lead_time_days: number;
}

export interface WorkshopCapacityResponse {
  workshop_id: number;
  product_type: string;
  available_qty: number;
  cost_per_unit: string;
  lead_time_days: number;
  updated_at: string;
}

export interface WorkshopCapacityListItem {
  product_type: string;
  available_qty: number;
  in_transit_qty: number;
  serving_capacity: number;
  cost_per_unit: string;
  lead_time_days: number;
  updated_at: string;
}

export interface NotificationItem {
  notification_id: number;
  order_id: number;
  sublot_id: number;
  product_type: string;
  qty_assigned: number;
  created_at: string;
}

export interface SubLotSummary {
  sublot_id: number;
  order_id: number;
  product_type: string;
  deadline: string;
  qty_assigned: number;
  delivered_qty: number | null;
  status: string;
}

export interface TrustEventSummary {
  sublot_id: number;
  on_time: boolean;
  defect_found: boolean;
  fault_party: string;
  date: string;
}

export interface TrustScoreResponse {
  workshop_id: number;
  score: number;
  grade: string;
  explanation: string[];
  history: TrustEventSummary[];
}

export const buyerApi = {
  placeOrder: (token: string, body: PlaceOrderRequest) =>
    request<PlaceOrderResponse>("/orders", { ...jsonBody(body), token }),

  listOrders: (token: string, page = 1, pageSize = 20, statusFilter?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (statusFilter) params.set("status", statusFilter);
    return request<OrderListResponse>(`/orders?${params.toString()}`, { token });
  },

  getOrder: (token: string, orderId: number) =>
    request<OrderStatusResponse>(`/orders/${orderId}`, { token }),

  getQuote: (token: string, orderId: number) =>
    request<OrderQuoteResponse>(`/orders/${orderId}/quote`, { token }),

  getInvoice: (token: string, orderId: number) =>
    request<SettlementSummaryResponse>(`/orders/${orderId}/invoice`, { token }),

  cancelOrder: (token: string, orderId: number) =>
    request<void>(`/orders/${orderId}`, { method: "DELETE", token }),

  flagDefect: (
    token: string,
    orderId: number,
    photo: File,
    defectQty: number,
    description: string,
    onProgress?: (pct: number) => void
  ) => {
    const form = new FormData();
    form.append("photo", photo);
    form.append("defect_qty", String(defectQty));
    form.append("description", description);
    return uploadWithProgress<{ order_id: number; defect_qty: number; verification_status: string }>(
      `/orders/${orderId}/flag-defect`,
      form,
      token,
      onProgress
    );
  },
};

export const workshopApi = {
  updateCapacity: (token: string, body: WorkshopCapacityUpdateRequest) =>
    request<WorkshopCapacityResponse>("/workshop/capacity", { ...jsonBody(body), token }),

  listCapacity: (token: string) =>
    request<WorkshopCapacityListItem[]>("/workshop/capacity", { token }),

  listSublots: (token: string) =>
    request<SubLotSummary[]>("/workshop/sublots", { token }),

  markDelivered: (token: string, sublotId: number, deliveredQty: number) =>
    request<{ sublot_id: number; delivered_qty: number }>(
      `/workshop/sublots/${sublotId}/deliver`,
      { ...jsonBody({ delivered_qty: deliveredQty }), token }
    ),

  startProduction: (token: string, sublotId: number) =>
    request<{ sublot_id: number; status: string }>(
      `/workshop/sublots/${sublotId}/start-production`,
      { method: "POST", token }
    ),

  listNotifications: (token: string) =>
    request<NotificationItem[]>("/workshop/notifications", { token }),

  uploadDefectPhoto: (token: string, sublotId: number, photo: File) => {
    const form = new FormData();
    form.append("photo", photo);
    return request<{ sublot_id: number; status: string }>(
      `/workshop/sublots/${sublotId}/photo`,
      { method: "POST", body: form, token }
    );
  },

  getTrustScore: (token: string) =>
    request<TrustScoreResponse>("/workshop/trust", { token }),
};

export interface ReviewItem {
  sublot_id: number;
  order_id: number;
  workshop_id: number;
  product_type: string;
  qty_assigned: number;
  status: string;
  updated_at: string;
  verdict: string | null;
  fault_party: string | null;
  confidence: number | null;
  explanation: string | null;
}

export const adminApi = {
  listNeedsReview: (token: string) => request<ReviewItem[]>("/admin/sublots/needs-review", { token }),

  retryVerification: (token: string, sublotId: number) =>
    request<{ sublot_id: number; status: string; explanation: string | null }>(
      `/admin/sublots/${sublotId}/retry-verification`,
      { method: "POST", token }
    ),
};

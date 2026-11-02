'use client';

import { apiFetch, apiFetchObjectUrl, clearToken, setToken } from './client';
import type {
  CommodityGroup,
  OrganizationInvite,
  OrganizationMemberWithProfile,
  Profile,
  ProcurementRequestWithDetails,
} from '@/types/database';

export { ApiError, getToken, clearToken } from './client';

// ============================================================================
// Types
// ============================================================================

export interface AuthUser {
  id: string;
  email: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface MeResponse {
  user: AuthUser;
  profile: Profile;
}

export type MemberRole = 'admin' | 'buyer' | 'requester';

export interface MyOrganization {
  id: string;
  name: string;
  slug: string;
  role: MemberRole;
  created_at: string;
  updated_at: string;
}

// Request fields an org may optionally mark as required (see backend settings).
export type ConfigurableRequiredField = 'vat_id' | 'department';

// A booking rule: purchases of `applies_to` are booked under that group,
// up to `max_total` (null = always).
export interface CommodityRule {
  applies_to: string;
  commodity_group_id: number;
  max_total: number | null;
}

export interface OrganizationSettings {
  required_fields: ConfigurableRequiredField[];
  commodity_rules?: CommodityRule[];
  // Show "AI suggested X, users chose Y" once it happened this many times.
  correction_alert_threshold?: number;
}

export interface CorrectionPattern {
  suggested: { id: number; category: string; name: string };
  chosen: { id: number; category: string; name: string };
  count: number;
  examples: string[];
}

export interface MembersResponse {
  members: OrganizationMemberWithProfile[];
  invites: OrganizationInvite[];
}

export interface OrderLineInput {
  positionDescription: string;
  unitPrice: number;
  amount: number;
  unit: string;
  totalPrice: number;
}

export interface CreateRequestInput {
  requestorName: string;
  titleShortDescription: string;
  vendorName: string;
  vatId: string;
  commodityGroupId: number;
  totalCost: number;
  department: string;
  orderLines: OrderLineInput[];
  // Catalog articles the form suggested per line, so the approver sees them.
  catalogAlternatives?: { lineIndex: number; articleIds: string[] }[];
  // The AI's suggested group; a different choice is logged as a correction.
  suggestedCommodityGroupId?: number | null;
}

export interface UpdateRequestInput {
  requestorName: string;
  title: string;
  vendorName: string;
  vatId: string;
  commodityGroupId: number;
  totalCost: number;
  department: string;
  orderLines: OrderLineInput[];
}

export interface ExtractedOrderLine extends OrderLineInput {
  // Discount (negative) or surcharge (positive) printed on the line, in percent.
  priceAdjustmentPercent: number | null;
}

// null = not found in the document (listed in ExtractionResponse.missingFields).
export interface ExtractedVendorData {
  title: string | null;
  vendorName: string | null;
  vatId: string | null;
  department: string | null;
  orderLines: ExtractedOrderLine[];
  // The quote's grand total, as printed.
  totalCost: number | null;
  // Shipping, fees and tax outside the order lines: lines + this = totalCost.
  additionalCharges: number;
  // Options the vendor offers instead of ordered lines; not part of totalCost.
  alternatives: ExtractedOrderLine[];
  commodityGroupId: number | null;
  commodityGroupName: string | null;
  // The organization's booking rule that set the group (e.g. "printer toner").
  commodityRule: string | null;
}

export interface ExtractionResponse {
  success: boolean;
  data?: ExtractedVendorData;
  error?: string;
  missingFields?: string[];
  // Inconsistencies the extraction couldn't resolve (e.g. totals don't add up).
  warnings?: ExtractionWarning[] | null;
}

export interface ExtractionWarning {
  // e.g. 'lines_vs_subtotal', 'line_math', 'vendor_is_customer'
  code: string;
  message: string;
}

export interface Supplier {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
  country: string | null;
  vat_id: string | null;
  website: string | null;
  email: string | null;
  created_at: string;
}

export interface Article {
  id: string;
  article_number: string;
  supplier_id: string;
  supplier_name: string | null;
  description: string;
  unit_price: string;
  currency: string;
  unit: string;
  quantity: string;
  created_at: string;
}

export interface ArticlePage {
  items: Article[];
  total: number;
  limit: number;
  offset: number;
}

// ============================================================================
// Auth
// ============================================================================

export const auth = {
  async signUp(params: { email: string; password: string; fullName: string }): Promise<TokenResponse> {
    const result = await apiFetch<TokenResponse>('/auth/signup', {
      body: { email: params.email, password: params.password, full_name: params.fullName },
    });
    setToken(result.access_token);
    return result;
  },

  async signIn(params: { email: string; password: string }): Promise<TokenResponse> {
    const result = await apiFetch<TokenResponse>('/auth/signin', { body: params });
    setToken(result.access_token);
    return result;
  },

  signOut(): void {
    clearToken();
  },

  me(): Promise<MeResponse> {
    return apiFetch<MeResponse>('/auth/me');
  },
};

// ============================================================================
// Requests
// ============================================================================

export interface RequestActivity {
  id: string;
  request_id: string;
  actor_id: string | null;
  action: 'created' | 'updated' | 'status_changed' | 'approved' | 'rejected' | 'document_attached';
  summary: string;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export const requests = {
  list(): Promise<ProcurementRequestWithDetails[]> {
    return apiFetch('/requests');
  },

  get(requestId: string): Promise<ProcurementRequestWithDetails> {
    return apiFetch(`/requests/${requestId}`);
  },

  getActivity(requestId: string): Promise<RequestActivity[]> {
    return apiFetch(`/requests/${requestId}/activity`);
  },

  uploadDocument(requestId: string, file: File): Promise<void> {
    const formData = new FormData();
    formData.append('file', file);
    return apiFetch(`/requests/${requestId}/document`, { method: 'PUT', formData });
  },

  /** Auth-fetched object URL for the stored PDF, or null if none attached. */
  documentUrl(requestId: string): Promise<string | null> {
    return apiFetchObjectUrl(`/requests/${requestId}/document`);
  },

  create(input: CreateRequestInput): Promise<ProcurementRequestWithDetails> {
    return apiFetch('/requests', { body: input });
  },

  update(requestId: string, input: UpdateRequestInput): Promise<ProcurementRequestWithDetails> {
    return apiFetch(`/requests/${requestId}`, { method: 'PUT', body: input });
  },

  updateStatus(
    requestId: string,
    status: 'open' | 'in_progress' | 'closed'
  ): Promise<ProcurementRequestWithDetails> {
    return apiFetch(`/requests/${requestId}/status`, { method: 'PATCH', body: { status } });
  },

  decideApproval(
    requestId: string,
    decision: 'approved' | 'rejected'
  ): Promise<ProcurementRequestWithDetails> {
    return apiFetch(`/requests/${requestId}/approval`, { method: 'PATCH', body: { decision } });
  },

  delete(requestId: string): Promise<void> {
    return apiFetch(`/requests/${requestId}`, { method: 'DELETE' });
  },
};

// ============================================================================
// Organizations
// ============================================================================

export const organizations = {
  create(params: { name: string; slug: string }): Promise<MyOrganization> {
    return apiFetch('/organizations', { body: params });
  },

  getMine(): Promise<MyOrganization> {
    return apiFetch('/organizations/me');
  },

  getMembers(): Promise<MembersResponse> {
    return apiFetch('/organizations/me/members');
  },

  invite(email: string, role: MemberRole = 'requester'): Promise<OrganizationInvite> {
    return apiFetch('/organizations/me/invites', { body: { email, role } });
  },

  cancelInvite(inviteId: string): Promise<void> {
    return apiFetch(`/organizations/me/invites/${inviteId}`, { method: 'DELETE' });
  },

  getCommodityCorrections(): Promise<CorrectionPattern[]> {
    return apiFetch('/organizations/me/commodity-corrections');
  },

  acceptInvite(token: string): Promise<MyOrganization> {
    return apiFetch('/organizations/invites/accept', { body: { token } });
  },

  getSettings(): Promise<OrganizationSettings> {
    return apiFetch('/organizations/me/settings');
  },

  // Only the settings sent are changed (the backend merges).
  updateSettings(settings: Partial<OrganizationSettings>): Promise<OrganizationSettings> {
    return apiFetch('/organizations/me/settings', { method: 'PATCH', body: settings });
  },
};

// ============================================================================
// Commodity groups
// ============================================================================

export const commodityGroups = {
  list(): Promise<CommodityGroup[]> {
    return apiFetch('/commodity-groups');
  },
};

// ============================================================================
// Suppliers
// ============================================================================

export const suppliers = {
  list(params?: { search?: string; category?: string }): Promise<Supplier[]> {
    const qs = new URLSearchParams();
    if (params?.search) qs.set('search', params.search);
    if (params?.category) qs.set('category', params.category);
    qs.set('limit', '500');
    return apiFetch(`/suppliers?${qs.toString()}`);
  },

  count(): Promise<{ count: number }> {
    return apiFetch('/suppliers/count');
  },
};

// ============================================================================
// Articles
// ============================================================================

export const articles = {
  list(params?: {
    search?: string;
    supplierId?: string;
    limit?: number;
    offset?: number;
  }): Promise<ArticlePage> {
    const qs = new URLSearchParams();
    if (params?.search) qs.set('search', params.search);
    if (params?.supplierId) qs.set('supplier_id', params.supplierId);
    qs.set('limit', String(params?.limit ?? 25));
    qs.set('offset', String(params?.offset ?? 0));
    return apiFetch(`/articles?${qs.toString()}`);
  },

  count(): Promise<{ count: number }> {
    return apiFetch('/articles/count');
  },

  // Negotiated catalog articles matching each line (one list per line, in order).
  suggest(
    lines: { description: string; unitPrice?: number; amount?: number; unit?: string }[],
    vendor?: { vendorName?: string; vendorTaxId?: string }
  ): Promise<{ suggestions: ArticleSuggestion[][] }> {
    return apiFetch('/articles/suggestions', { body: { lines, ...vendor } });
  },
};

export interface ArticleSuggestion {
  article_id: string;
  article_number: string;
  description: string;
  supplier_name: string;
  unit_price: number;
  unit: string;
  quantity: number;
  // The model's view; the requester always reviews equivalence.
  match: 'same' | 'comparable';
  // Negotiated with the request's own vendor (the quote should use this price).
  same_vendor: boolean;
  // Priced in the same unit as the line, so the prices can be compared.
  same_unit: boolean;
  // The kind of product the line was read as (why it matched), e.g. "laptop".
  matched_as: string;
}

// ============================================================================
// PDF + extraction
// ============================================================================

export const pdf = {
  parse(file: File): Promise<{ text: string }> {
    const formData = new FormData();
    formData.append('file', file);
    return apiFetch('/pdf/parse', { formData });
  },
};

export const extraction = {
  extract(text: string): Promise<ExtractionResponse> {
    return apiFetch('/extraction', { body: { text } });
  },
};

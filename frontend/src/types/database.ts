// ============================================================================
// Database Type Definitions
// ============================================================================

export type Profile = {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  created_at: string;
  updated_at: string;
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
  created_at: string;
  updated_at: string;
};

export type OrganizationMember = {
  id: string;
  organization_id: string;
  user_id: string;
  role: 'admin' | 'buyer' | 'requester';
  created_at: string;
};

export type OrganizationInvite = {
  id: string;
  organization_id: string;
  email: string;
  role: 'admin' | 'buyer' | 'requester';
  invited_by: string;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
};

export type CommodityGroup = {
  id: number;
  category: string;
  name: string;
  // Set for a group only your organization uses; null = shared taxonomy.
  organization_id?: string | null;
  created_at?: string;
};

export type ProcurementRequest = {
  id: string;
  user_id: string;
  organization_id: string | null;
  created_by: string | null;
  requestor_name: string;
  title: string;
  vendor_name: string;
  vat_id: string | null;
  commodity_group_id: number | null;
  department: string | null;
  status: 'open' | 'in_progress' | 'closed';
  approval_status: 'pending' | 'approved' | 'rejected';
  approved_by: string | null;
  approved_at: string | null;
  total_cost: number | null;
  created_at: string;
  updated_at: string;
};

export type OrderLine = {
  id: string;
  request_id: string;
  position_description: string;
  unit_price: number;
  amount: number;
  unit: string;
  total_price: number;
  line_order: number;
  created_at: string;
};

// ============================================================================
// Helper Types for Joined Queries
// ============================================================================

// Negotiated catalog articles that were suggested when the request was created
// (snapshot, prices as at that time) — shown to the approver.
export type CatalogAlternative = {
  line_description: string;
  quoted_unit_price: number;
  unit: string;
  options: {
    supplier_name: string;
    article_number: string;
    description: string;
    unit_price: number;
    unit: string;
  }[];
};

export type ProcurementRequestWithDetails = ProcurementRequest & {
  order_lines: OrderLine[];
  commodity_groups: CommodityGroup | null;
  catalog_alternatives?: CatalogAlternative[] | null;
};

export type OrganizationWithRole = Organization & {
  role: 'owner' | 'admin' | 'member';
};

export type OrganizationMemberWithProfile = OrganizationMember & {
  profiles: Profile | null;
};


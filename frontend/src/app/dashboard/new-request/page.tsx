'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert, Loader } from '@/components/base';

interface OrderLine {
  positionDescription: string;
  unitPrice: number;
  amount: number;
  unit: string;
  totalPrice: number;
  // Discount (negative) / surcharge (positive) from the quote, kept on edits.
  priceAdjustmentPercent?: number | null;
}

// Rounding slack when comparing money amounts.
const CENTS_TOLERANCE = 0.05;

// Totals warnings the form re-checks live (below), so the backend's one-off
// message isn't shown on top.
const TOTALS_CHECKED_LIVE = ['lines_vs_subtotal', 'lines_vs_grand_total'];

// Fields the extraction may report missing that are optional unless the
// organization requires them (backend label -> organization setting).
const OPTIONAL_FIELDS: Record<string, api.ConfigurableRequiredField> = {
  'Tax ID': 'vat_id',
  Department: 'department',
};

function lineTotal(line: OrderLine): number {
  const adjustment = line.priceAdjustmentPercent ?? 0;
  return Math.round(line.unitPrice * line.amount * (1 + adjustment / 100) * 100) / 100;
}

function formatMoney(value: number): string {
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function NewRequestPage() {
  const router = useRouter();
  const { data: commodityGroups } = useCommodityGroups();

  // Form state
  const [requestorName, setRequestorName] = useState('');
  const [title, setTitle] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [vatId, setVatId] = useState('');
  const [department, setDepartment] = useState('');
  const [commodityGroupId, setCommodityGroupId] = useState<number | null>(null);
  const [orderLines, setOrderLines] = useState<OrderLine[]>([]);
  // Shipping, tax and fees outside the lines; total = lines + these (the full
  // amount Acme pays, which approval decisions depend on).
  const [additionalCharges, setAdditionalCharges] = useState<number>(0);
  // The uploaded quote's grand total, to check the form against while editing.
  const [quoteTotal, setQuoteTotal] = useState<number | null>(null);
  // Options the vendor offered instead of ordered items (not in the quote's total).
  const [alternatives, setAlternatives] = useState<OrderLine[]>([]);

  const { data: orgSettings } = useQuery({
    queryKey: ['organization-settings'],
    queryFn: () => api.organizations.getSettings(),
  });

  const totalCost =
    Math.round(
      (orderLines.reduce((sum, line) => sum + line.totalPrice, 0) + additionalCharges) * 100
    ) / 100;

  // Live check against the quote: if the form no longer adds up to the quote's
  // total, say by how much and point at the line that explains the gap.
  const totalsMismatch = useMemo(() => {
    if (quoteTotal === null || Math.abs(totalCost - quoteTotal) <= CENTS_TOLERANCE) {
      return null;
    }
    const difference = Math.round((totalCost - quoteTotal) * 100) / 100;
    const suspects = orderLines
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => Math.abs(line.totalPrice - difference) <= CENTS_TOLERANCE);
    return { difference, suspectIndex: suspects.length === 1 ? suspects[0].index : null };
  }, [orderLines, quoteTotal, totalCost]);

  // UI state
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [warning, setWarning] = useState('');
  const [extracting, setExtracting] = useState(false);

  // PDF parsing mutation
  const parsePDFMutation = useMutation({
    mutationFn: async (file: File) => {
      const result = await api.pdf.parse(file);
      if (!result.text) {
        throw new Error('Failed to parse PDF');
      }
      return result.text;
    },
  });

  // Handle file upload and extraction
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = e.target.files?.[0];
    if (!uploadedFile) return;

    // Browsers leave the MIME type empty for some files (e.g. "Quote.Pdf"), so
    // also accept by extension; the backend verifies the actual file bytes.
    const isPdf =
      uploadedFile.type === 'application/pdf' || /\.pdf$/i.test(uploadedFile.name);
    if (!isPdf) {
      setError('Please upload a PDF file');
      return;
    }

    setFile(uploadedFile);
    setError('');
    setSuccess('');
    setWarning('');
    setQuoteTotal(null);
    setAlternatives([]);
    setExtracting(true);

    try {
      // Step 1: Parse PDF
      const pdfText = await parsePDFMutation.mutateAsync(uploadedFile);

      // Step 2: Extract structured data and classify the commodity group
      const extractionResult = await api.extraction.extract(pdfText);
      
      if (!extractionResult.success || !extractionResult.data) {
        throw new Error(extractionResult.error || 'Failed to extract data');
      }

      const extracted = extractionResult.data;

      // Step 3: Auto-fill form (including commodity group from extraction)
      setTitle(extracted.title || '');
      setVendorName(extracted.vendorName || '');
      setVatId(extracted.vatId || '');
      setDepartment(extracted.department || '');
      setOrderLines(extracted.orderLines || []);
      setAdditionalCharges(extracted.additionalCharges || 0);
      setQuoteTotal(extracted.totalCost ?? null);
      setAlternatives(extracted.alternatives || []);

      // Set commodity group (now included in extraction)
      if (extracted.commodityGroupId) {
        setCommodityGroupId(extracted.commodityGroupId);
      }

      // Missing fields: only those the request actually needs. Optional fields
      // (e.g. Department, rarely on a quote) are simply left empty.
      const required = orgSettings?.required_fields ?? [];
      const missing = (extractionResult.missingFields ?? []).filter((label) => {
        const optional = OPTIONAL_FIELDS[label];
        return !optional || required.includes(optional);
      });
      const warnings: string[] = [];
      if (missing.length > 0) {
        warnings.push(`⚠️ Not found on the quote: ${missing.join(', ')}. Please fill these in.`);
      }
      for (const issue of extractionResult.warnings ?? []) {
        if (!TOTALS_CHECKED_LIVE.includes(issue.code)) {
          warnings.push(`⚠️ Please double-check: ${issue.message}`);
        }
      }
      setWarning(warnings.join('\n'));

      setSuccess('✓ PDF extracted successfully! Review and submit.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error('Extraction error:', err);
      setError(err instanceof Error ? err.message : 'Failed to extract data from PDF');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } finally {
      setExtracting(false);
    }
  };

  // Add order line manually
  const addOrderLine = () => {
    setOrderLines([
      ...orderLines,
      {
        positionDescription: '',
        unitPrice: 0,
        amount: 1,
        unit: '',
        totalPrice: 0,
      },
    ]);
  };

  // Update order line
  const updateOrderLine = (index: number, field: keyof OrderLine, value: string | number) => {
    const updated = [...orderLines];
    updated[index] = { ...updated[index], [field]: value };
    
    // Auto-calculate total price (keeping the quote's discount/surcharge)
    if (field === 'unitPrice' || field === 'amount' || field === 'priceAdjustmentPercent') {
      updated[index].totalPrice = lineTotal(updated[index]);
    }

    setOrderLines(updated);
  };

  // Order an alternative the vendor offered. The quote's printed total (and its
  // tax) no longer applies, so stop checking against it rather than raise a
  // false "doesn't add up" warning.
  const orderAlternative = (index: number) => {
    setOrderLines([...orderLines, alternatives[index]]);
    setAlternatives(alternatives.filter((_, i) => i !== index));
    setQuoteTotal(null);
  };

  // Remove order line
  const removeOrderLine = (index: number) => {
    setOrderLines(orderLines.filter((_, i) => i !== index));
  };

  // Submit form
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setWarning('');

    // Validation with scroll to top on error
    if (!commodityGroupId) {
      setError('Please select a commodity group');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    if (orderLines.length === 0) {
      setError('Please add at least one order line');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    try {
      const created = await api.requests.create({
        requestorName,
        titleShortDescription: title,
        vendorName,
        vatId,
        commodityGroupId: commodityGroupId,
        totalCost,
        department,
        orderLines: orderLines.map(({ positionDescription, unitPrice, amount, unit, totalPrice }) => ({
          positionDescription, unitPrice, amount, unit, totalPrice,
        })),
      });

      // Attach the original PDF (if one was uploaded) so it shows on the detail page.
      if (file) {
        try {
          await api.requests.uploadDocument(created.id, file);
        } catch {
          // Non-fatal — the request itself was created.
        }
      }

      setSuccess('Request created successfully! Redirecting...');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      setTimeout(() => {
        router.push('/dashboard/requests');
        router.refresh();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create request');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">New Procurement Request</h1>
        <p className="text-gray-600 mt-2">
          Upload a vendor offer PDF or fill in the details manually
        </p>
      </div>

      {error && <Alert variant="error" className="mb-6">{error}</Alert>}
      {warning && <Alert variant="warning" className="mb-6 whitespace-pre-line">{warning}</Alert>}
      {totalsMismatch && (
        <Alert variant="warning" className="mb-6">
          ⚠️ The order lines don&apos;t add up to the quote&apos;s total — see the note under Total Cost
          {totalsMismatch.suspectIndex !== null ? ' and the highlighted line' : ''}.
        </Alert>
      )}
      {success && <Alert variant="success" className="mb-6">{success}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* PDF Upload Section */}
        <div className="lio-card p-6">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Upload Vendor Offer (Optional)</h2>
          <p className="text-sm text-gray-600 mb-4">
            Upload a PDF to automatically extract vendor details and order items
          </p>

          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-accent-deep/50 transition-colors">
            <input
              type="file"
              accept="application/pdf,.pdf"
              onChange={handleFileChange}
              className="hidden"
              id="pdf-upload"
              disabled={extracting}
            />
            <label htmlFor="pdf-upload" className="cursor-pointer">
              {extracting ? (
                <div className="flex flex-col items-center">
                  <Loader size="lg" />
                  <p className="mt-4 text-sm text-gray-600">Extracting data from PDF...</p>
                </div>
              ) : file ? (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-green-500"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                  <p className="mt-2 text-sm font-medium text-gray-900">{file.name}</p>
                  <p className="mt-1 text-xs text-gray-500">Click to upload a different file</p>
                </div>
              ) : (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-gray-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                  <p className="mt-2 text-sm text-gray-600">
                    Click to upload or drag and drop
                  </p>
                  <p className="mt-1 text-xs text-gray-500">PDF up to 10MB</p>
                </div>
              )}
            </label>
          </div>
        </div>

        {/* Request Details */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Request Details</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Requestor Name *"
              value={requestorName}
              onChange={(e) => setRequestorName(e.target.value)}
              required
              placeholder="John Doe"
            />

            <Input
              label="Department"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="IT Department"
            />
          </div>

          <Input
            label="Title / Short Description *"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            placeholder="Adobe Creative Cloud Subscription"
          />
        </div>

        {/* Vendor Information */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Vendor Information</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Vendor Name *"
              value={vendorName}
              onChange={(e) => setVendorName(e.target.value)}
              required
              placeholder="Adobe Systems"
            />

            <Input
              label="Tax ID"
              value={vatId}
              onChange={(e) => setVatId(e.target.value)}
              placeholder="12-3456789"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Commodity Group *
            </label>
            <select
              value={commodityGroupId || ''}
              onChange={(e) => setCommodityGroupId(Number(e.target.value))}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900 focus:ring-2 focus:ring-accent-deep/60 focus:border-accent-deep/40"
            >
              <option value="">
                {commodityGroups ? 'Select a commodity group' : 'Loading...'}
              </option>
              {commodityGroups?.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.id} - {group.category} - {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Order Lines */}
        <div className="lio-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold tracking-tight text-ink">Order Lines *</h2>
            <Button type="button" onClick={addOrderLine} variant="outline" size="sm">
              + Add Line
            </Button>
          </div>

          {orderLines.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              No order lines yet. Add one to get started.
            </div>
          ) : (
            <div className="space-y-4">
              {orderLines.map((line, index) => (
                <div
                  key={index}
                  className={`border rounded-lg p-4 ${
                    totalsMismatch?.suspectIndex === index
                      ? 'border-amber-400 bg-amber-50'
                      : 'border-gray-200'
                  }`}
                >
                  <div className="flex items-start justify-between mb-4">
                    <h3 className="text-sm font-medium text-gray-700">Line {index + 1}</h3>
                    <button
                      type="button"
                      onClick={() => removeOrderLine(index)}
                      className="text-red-600 hover:text-red-700 text-sm"
                    >
                      Remove
                    </button>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-6 gap-4">
                    <div className="lg:col-span-2">
                      <Input
                        label="Description"
                        value={line.positionDescription}
                        onChange={(e) =>
                          updateOrderLine(index, 'positionDescription', e.target.value)
                        }
                        required
                        placeholder="Item description"
                      />
                    </div>

                    <Input
                      label="Unit Price ($)"
                      type="number"
                      step="0.01"
                      value={line.unitPrice}
                      onChange={(e) =>
                        updateOrderLine(index, 'unitPrice', parseFloat(e.target.value) || 0)
                      }
                      required
                    />

                    <Input
                      label="Amount"
                      type="number"
                      step="0.01"
                      value={line.amount}
                      onChange={(e) =>
                        updateOrderLine(index, 'amount', parseFloat(e.target.value) || 1)
                      }
                      required
                    />

                    <Input
                      label="Unit"
                      value={line.unit}
                      onChange={(e) => updateOrderLine(index, 'unit', e.target.value)}
                      required
                      placeholder="pcs, licenses, etc."
                    />

                    {/* Shown as a positive discount; stored signed (−20 = 20% off). */}
                    <Input
                      label="Discount %"
                      type="number"
                      step="0.01"
                      value={line.priceAdjustmentPercent ? -line.priceAdjustmentPercent : ''}
                      onChange={(e) => {
                        const discount = parseFloat(e.target.value);
                        updateOrderLine(
                          index,
                          'priceAdjustmentPercent',
                          Number.isNaN(discount) || discount === 0 ? 0 : -discount
                        );
                      }}
                      placeholder="0"
                    />
                  </div>

                  <div className="mt-2 flex items-center justify-between gap-4">
                    <span className="text-sm text-amber-800">
                      {totalsMismatch?.suspectIndex === index &&
                        "Not part of the quote's total — probably an alternative or optional item."}
                    </span>
                    <span>
                      <span className="text-sm text-gray-600">Total: </span>
                      <span className="text-lg font-semibold tracking-tight text-ink">
                        {formatMoney(line.totalPrice)}
                      </span>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {alternatives.length > 0 && (
            <div className="mt-4 border border-dashed border-gray-300 rounded-lg p-4">
              <h3 className="text-sm font-medium text-gray-700">
                Alternatives offered on this quote
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                Not included in the quote&apos;s total. Use one to add it to the order, then
                remove the line it replaces and check shipping, tax &amp; fees.
              </p>
              <ul className="space-y-2">
                {alternatives.map((alternative, index) => (
                  <li key={index} className="flex items-center justify-between gap-4">
                    <span className="text-sm text-gray-800">{alternative.positionDescription}</span>
                    <span className="flex items-center gap-3 shrink-0">
                      <span className="text-sm font-medium text-ink">
                        {formatMoney(alternative.totalPrice)}
                      </span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => orderAlternative(index)}
                      >
                        Use this
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4 items-end">
            <p className="text-sm text-gray-600">
              Shipping, tax and fees charged on the quote outside the line items.
            </p>
            <Input
              label="Shipping, tax & fees ($)"
              type="number"
              step="0.01"
              value={additionalCharges}
              onChange={(e) => setAdditionalCharges(parseFloat(e.target.value) || 0)}
            />
          </div>
        </div>

        {/* Total Cost */}
        <div className="bg-accent-soft/20 border-2 border-accent/40 rounded-lg p-6 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-lg font-medium text-gray-900">Total Cost</span>
            <span className="text-3xl font-bold text-accent-deep">
              {formatMoney(totalCost)}
            </span>
          </div>
          {totalsMismatch && quoteTotal !== null && (
            <Alert variant="warning">
              {totalsMismatch.suspectIndex !== null
                ? `Line ${totalsMismatch.suspectIndex + 1} ("${
                    orderLines[totalsMismatch.suspectIndex].positionDescription
                  }", ${formatMoney(totalsMismatch.difference)}) isn't included in the quote's total of ${formatMoney(
                    quoteTotal
                  )}. It's probably an alternative or optional item — remove it if you're not ordering it.`
                : `This adds up to ${formatMoney(totalCost)}, but the quote's total is ${formatMoney(
                    quoteTotal
                  )} (difference ${formatMoney(totalsMismatch.difference)}). Please check the lines against the quote.`}
            </Alert>
          )}
        </div>

        {/* Submit */}
        <div className="flex items-center justify-end space-x-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
          >
            Cancel
          </Button>
          <Button type="submit" size="lg">
            Create Request
          </Button>
        </div>
      </form>
    </div>
  );
}


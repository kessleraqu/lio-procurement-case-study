'use client';

import { useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { Alert, Button } from '@/components/base';

const money = (n: number) =>
  `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** Admin setting: requests below this total are approved without a buyer. */
export function ApprovalLimit() {
  const [saved, setSaved] = useState<number | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.organizations
      .getSettings()
      .then((s) => {
        setSaved(s.auto_approve_below ?? null);
        setDraft(s.auto_approve_below ? String(s.auto_approve_below) : '');
      })
      .catch(() => setError('Failed to load the approval setting'));
  }, []);

  const save = async (value: number | null) => {
    setSaving(true);
    setError('');
    try {
      const result = await api.organizations.updateSettings({ auto_approve_below: value });
      setSaved(result.auto_approve_below ?? null);
      setDraft(result.auto_approve_below ? String(result.auto_approve_below) : '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const parsed = parseFloat(draft);
  const draftValue = draft.trim() && parsed > 0 ? parsed : null;
  const dirty = draftValue !== saved;

  return (
    <div className="lio-card p-6 mt-6">
      <h2 className="text-xl font-semibold tracking-tight text-ink">Approvals</h2>
      <p className="text-sm text-ink/55 mt-1">
        Small purchases can skip the buyer. The limit is compared with the request&apos;s full
        total, including tax and shipping.
      </p>

      {error && (
        <Alert variant="error" className="mt-3">
          {error}
        </Alert>
      )}

      {/* What happens today, at a glance */}
      {saved ? (
        <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 px-4 py-3">
            <p className="text-xs font-medium uppercase tracking-wide text-emerald-800">
              Below {money(saved)}
            </p>
            <p className="mt-1 text-sm text-emerald-900">
              Approved automatically — no buyer needed
            </p>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3">
            <p className="text-xs font-medium uppercase tracking-wide text-amber-800">
              {money(saved)} and above
            </p>
            <p className="mt-1 text-sm text-amber-900">A buyer or admin approves the request</p>
          </div>
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-line px-4 py-3 text-sm text-ink/70">
          No limit set — every request is approved by a buyer or admin.
        </div>
      )}

      {/* Change it */}
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-sm text-ink">
          <span className="block mb-1 text-ink/70">Auto-approve requests below</span>
          <span className="flex items-center rounded-lg border border-gray-300 bg-white focus-within:ring-2 focus-within:ring-accent-deep/60">
            <span className="pl-3 text-gray-500">$</span>
            <input
              type="number"
              min={0}
              step="0.01"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="no limit"
              className="w-36 px-2 py-2 rounded-lg text-gray-900 outline-none"
            />
          </span>
        </label>
        <Button size="sm" onClick={() => save(draftValue)} disabled={!dirty} loading={saving}>
          Save
        </Button>
        {saved !== null && (
          <Button size="sm" variant="outline" onClick={() => save(null)} disabled={saving}>
            Remove limit
          </Button>
        )}
      </div>
    </div>
  );
}

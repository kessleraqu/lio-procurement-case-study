'use client';

import { useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert } from '@/components/base';

interface Draft {
  index: number | null; // null = new rule
  appliesTo: string;
  groupId: number | null;
  maxTotal: string;
}

const EMPTY_DRAFT: Draft = { index: null, appliesTo: '', groupId: null, maxTotal: '' };

/**
 * Admin settings for booking rules ("purchases of X are always booked under
 * group Y") and the "frequent corrections" hint that a rule may be missing.
 */
export function BookingRules() {
  const { data: groups } = useCommodityGroups();
  const [rules, setRules] = useState<api.CommodityRule[]>([]);
  const [threshold, setThreshold] = useState(3);
  const [corrections, setCorrections] = useState<api.CorrectionPattern[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    try {
      const [settings, patterns] = await Promise.all([
        api.organizations.getSettings(),
        api.organizations.getCommodityCorrections(),
      ]);
      setRules(settings.commodity_rules ?? []);
      setThreshold(settings.correction_alert_threshold ?? 3);
      setCorrections(patterns);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load booking rules');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const groupLabel = (id: number) => {
    const group = groups?.find((g) => g.id === id);
    return group ? `${group.category} · ${group.name}` : `Group #${id}`;
  };

  const save = async (settings: Partial<api.OrganizationSettings>) => {
    setSaving(true);
    setError('');
    try {
      const saved = await api.organizations.updateSettings(settings);
      setRules(saved.commodity_rules ?? []);
      setThreshold(saved.correction_alert_threshold ?? 3);
      setCorrections(await api.organizations.getCommodityCorrections());
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const saveDraft = async () => {
    if (!draft || !draft.appliesTo.trim() || draft.groupId === null) {
      setError('Describe what the rule covers and pick a commodity group');
      return;
    }
    const maxTotal = draft.maxTotal.trim() ? parseFloat(draft.maxTotal) : null;
    const rule: api.CommodityRule = {
      applies_to: draft.appliesTo.trim(),
      commodity_group_id: draft.groupId,
      max_total: maxTotal && maxTotal > 0 ? maxTotal : null,
    };
    const next =
      draft.index === null
        ? [...rules, rule]
        : rules.map((r, i) => (i === draft.index ? rule : r));
    if (await save({ commodity_rules: next })) setDraft(null);
  };

  const removeRule = (index: number) => {
    if (window.confirm(`Delete the rule for “${rules[index].applies_to}”?`)) {
      save({ commodity_rules: rules.filter((_, i) => i !== index) });
    }
  };

  return (
    <>
      {/* Booking rules */}
      <div className="lio-card p-6 mt-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-ink">Booking Rules</h2>
            <p className="text-sm text-ink/55 mt-1">
              Purchases of these kinds are always booked under the given commodity group,
              whatever the AI would pick otherwise. Requesters see when a rule applied and can
              still change the group for an exception.
            </p>
          </div>
          {!draft && (
            <Button size="sm" onClick={() => setDraft(EMPTY_DRAFT)}>
              + Add rule
            </Button>
          )}
        </div>

        {error && (
          <Alert variant="error" className="mt-4">
            {error}
          </Alert>
        )}

        <ul className="mt-4 space-y-2">
          {rules.map((rule, index) => (
            <li
              key={`${rule.applies_to}-${index}`}
              className="flex items-center gap-4 rounded-xl border border-line px-4 py-3"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm text-ink">
                  <span className="text-ink/55">Purchases of </span>
                  <span className="font-medium">&ldquo;{rule.applies_to}&rdquo;</span>
                </p>
                <p className="text-sm text-ink/70 mt-0.5">
                  → booked under <span className="font-medium text-ink">{groupLabel(rule.commodity_group_id)}</span>
                  {rule.max_total !== null && (
                    <span className="ml-2 rounded-full bg-ink-800/[0.05] px-2 py-0.5 text-xs text-ink/70">
                      only up to ${rule.max_total.toLocaleString('en-US')} per request
                    </span>
                  )}
                </p>
              </div>
              <button
                type="button"
                className="text-sm text-ink/60 hover:text-ink"
                onClick={() =>
                  setDraft({
                    index,
                    appliesTo: rule.applies_to,
                    groupId: rule.commodity_group_id,
                    maxTotal: rule.max_total?.toString() ?? '',
                  })
                }
              >
                Edit
              </button>
              <button
                type="button"
                className="text-sm text-red-600 hover:text-red-700"
                onClick={() => removeRule(index)}
                disabled={saving}
              >
                Delete
              </button>
            </li>
          ))}
          {rules.length === 0 && !draft && (
            <li className="text-sm text-ink/55">No booking rules yet.</li>
          )}
        </ul>

        {draft && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-accent-soft/10 p-4 space-y-3">
            <p className="text-sm font-medium text-ink">
              {draft.index === null ? 'New rule' : 'Edit rule'}
            </p>
            <Input
              label="Purchases of…"
              value={draft.appliesTo}
              onChange={(e) => setDraft({ ...draft, appliesTo: e.target.value })}
              placeholder="e.g. printer toner"
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                …are booked under
              </label>
              <select
                value={draft.groupId ?? ''}
                onChange={(e) =>
                  setDraft({ ...draft, groupId: e.target.value ? Number(e.target.value) : null })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900"
              >
                <option value="">Select a commodity group</option>
                {groups?.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.category} · {group.name}
                  </option>
                ))}
              </select>
            </div>
            <Input
              label="Only up to ($ per request, optional)"
              type="number"
              step="0.01"
              value={draft.maxTotal}
              onChange={(e) => setDraft({ ...draft, maxTotal: e.target.value })}
              placeholder="e.g. 500 for “small” purchases"
            />
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button size="sm" onClick={saveDraft} loading={saving}>
                Save rule
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Frequent corrections */}
      <div className="lio-card p-6 mt-6">
        <h2 className="text-xl font-semibold tracking-tight text-ink">Frequent Corrections</h2>
        <p className="text-sm text-ink/55 mt-1">
          When requesters keep replacing the suggested commodity group the same way, a booking
          rule may be missing.
        </p>
        <div className="mt-4 flex items-center gap-2 text-sm text-ink">
          <span>Show a correction after it happened</span>
          <input
            type="number"
            min={1}
            max={100}
            value={threshold}
            onChange={(e) => setThreshold(Math.max(1, parseInt(e.target.value) || 1))}
            onBlur={() => save({ correction_alert_threshold: threshold })}
            className="w-16 px-2 py-1 border border-gray-300 rounded-lg text-gray-900"
          />
          <span>times</span>
        </div>

        <ul className="mt-4 space-y-2">
          {corrections.map((c) => (
            <li
              key={`${c.suggested.id}-${c.chosen.id}`}
              className="flex items-center gap-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3"
            >
              <div className="flex-1 min-w-0">
                <p className="text-sm text-ink">
                  Suggested <span className="font-medium">{c.suggested.name}</span> → requesters
                  chose <span className="font-medium">{c.chosen.name}</span>
                  <span className="ml-2 text-ink/55">{c.count}×</span>
                </p>
                <p className="text-xs text-ink/55 mt-0.5 truncate">
                  e.g. {c.examples.map((t) => `“${t}”`).join(', ')}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  setDraft({ index: null, appliesTo: '', groupId: c.chosen.id, maxTotal: '' })
                }
              >
                Create rule
              </Button>
            </li>
          ))}
          {corrections.length === 0 && (
            <li className="text-sm text-ink/55">No repeated corrections yet.</li>
          )}
        </ul>
      </div>
    </>
  );
}

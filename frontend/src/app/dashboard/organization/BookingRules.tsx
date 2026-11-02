'use client';

import { useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert } from '@/components/base';
import type { CommodityGroup } from '@/types/database';

interface RuleDraft {
  index: number | null; // null = new rule
  appliesTo: string;
  groupId: number | null;
  maxTotal: string;
}

interface GroupDraft {
  id: number | null; // null = new group
  category: string;
  name: string;
}

/**
 * Admin settings: the organization's own commodity groups, its booking rules
 * ("purchases of X are always booked under group Y"), and the "frequent
 * corrections" hint that a rule may be missing.
 */
export function BookingRules() {
  const queryClient = useQueryClient();
  const { data: groups } = useCommodityGroups();
  const [rules, setRules] = useState<api.CommodityRule[]>([]);
  const [threshold, setThreshold] = useState(3);
  const [corrections, setCorrections] = useState<api.CorrectionPattern[]>([]);
  const [ruleDraft, setRuleDraft] = useState<RuleDraft | null>(null);
  const [groupDraft, setGroupDraft] = useState<GroupDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const ownGroups = useMemo(() => (groups ?? []).filter((g) => g.organization_id), [groups]);
  const categories = useMemo(
    () => Array.from(new Set((groups ?? []).map((g) => g.category))).sort(),
    [groups]
  );

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
    return group ? `${group.category} · ${group.name}` : `Group ${id}`;
  };

  // Run a change, show its error if any; true on success.
  const run = async (action: () => Promise<unknown>) => {
    setSaving(true);
    setError('');
    try {
      await action();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const saveSettings = (settings: Partial<api.OrganizationSettings>) =>
    run(async () => {
      const saved = await api.organizations.updateSettings(settings);
      setRules(saved.commodity_rules ?? []);
      setThreshold(saved.correction_alert_threshold ?? 3);
      setCorrections(await api.organizations.getCommodityCorrections());
    });

  // --- Own commodity groups -------------------------------------------------

  const refreshGroups = () => queryClient.invalidateQueries({ queryKey: ['commodity-groups'] });

  const saveGroup = async () => {
    if (!groupDraft || !groupDraft.category.trim() || !groupDraft.name.trim()) {
      setError('A commodity group needs a category and a name');
      return;
    }
    const body = { category: groupDraft.category.trim(), name: groupDraft.name.trim() };
    const ok = await run(() =>
      groupDraft.id === null
        ? api.commodityGroups.create(body)
        : api.commodityGroups.update(groupDraft.id, body)
    );
    if (ok) {
      setGroupDraft(null);
      await refreshGroups();
    }
  };

  const removeGroup = async (group: CommodityGroup) => {
    if (!window.confirm(`Delete the commodity group “${group.name}”?`)) return;
    if (await run(() => api.commodityGroups.remove(group.id))) await refreshGroups();
  };

  // --- Booking rules ----------------------------------------------------------

  const saveRule = async () => {
    if (!ruleDraft || !ruleDraft.appliesTo.trim() || ruleDraft.groupId === null) {
      setError('Describe what the rule covers and pick a commodity group');
      return;
    }
    const maxTotal = ruleDraft.maxTotal.trim() ? parseFloat(ruleDraft.maxTotal) : null;
    const rule: api.CommodityRule = {
      applies_to: ruleDraft.appliesTo.trim(),
      commodity_group_id: ruleDraft.groupId,
      max_total: maxTotal && maxTotal > 0 ? maxTotal : null,
    };
    const next =
      ruleDraft.index === null
        ? [...rules, rule]
        : rules.map((r, i) => (i === ruleDraft.index ? rule : r));
    if (await saveSettings({ commodity_rules: next })) setRuleDraft(null);
  };

  const removeRule = (index: number) => {
    if (window.confirm(`Delete the rule for “${rules[index].applies_to}”?`)) {
      saveSettings({ commodity_rules: rules.filter((_, i) => i !== index) });
    }
  };

  return (
    <>
      {error && (
        <Alert variant="error" className="mt-6">
          {error}
        </Alert>
      )}

      {/* Own commodity groups */}
      <div className="lio-card p-6 mt-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold tracking-tight text-ink">
              Your Commodity Groups
            </h2>
            <p className="text-sm text-ink/55 mt-1">
              Groups only your organization uses, next to the shared list. They work like any
              other group: in requests, the AI&apos;s suggestions and booking rules.
            </p>
          </div>
          {!groupDraft && (
            <Button
              size="sm"
              className="shrink-0 whitespace-nowrap"
              onClick={() => setGroupDraft({ id: null, category: '', name: '' })}
            >
              + Add group
            </Button>
          )}
        </div>

        <ul className="mt-4 space-y-2">
          {ownGroups.map((group) => (
            <li
              key={group.id}
              className="flex items-center gap-4 rounded-xl border border-line px-4 py-3"
            >
              <span className="w-20 shrink-0 font-mono text-xs text-ink/55">{group.id}</span>
              <span className="flex-1 min-w-0 text-sm text-ink">
                <span className="text-ink/55">{group.category} · </span>
                <span className="font-medium">{group.name}</span>
              </span>
              <button
                type="button"
                className="text-sm text-ink/60 hover:text-ink"
                onClick={() =>
                  setGroupDraft({ id: group.id, category: group.category, name: group.name })
                }
              >
                Edit
              </button>
              <button
                type="button"
                className="text-sm text-red-600 hover:text-red-700"
                onClick={() => removeGroup(group)}
                disabled={saving}
              >
                Delete
              </button>
            </li>
          ))}
          {ownGroups.length === 0 && !groupDraft && (
            <li className="text-sm text-ink/55">No groups of your own yet.</li>
          )}
        </ul>

        {groupDraft && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-accent-soft/10 p-4 space-y-3">
            <p className="text-sm font-medium text-ink">
              {groupDraft.id === null ? 'New commodity group' : `Edit group ${groupDraft.id}`}
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <Input
                  label="Category"
                  list="commodity-categories"
                  value={groupDraft.category}
                  onChange={(e) => setGroupDraft({ ...groupDraft, category: e.target.value })}
                  placeholder="e.g. Office & Administration"
                />
                <datalist id="commodity-categories">
                  {categories.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>
              <Input
                label="Name"
                value={groupDraft.name}
                onChange={(e) => setGroupDraft({ ...groupDraft, name: e.target.value })}
                placeholder="e.g. Office Supplies"
              />
            </div>
            <p className="text-xs text-ink/55">
              {groupDraft.id === null
                ? 'A code from 100000 up is assigned automatically.'
                : 'Renaming keeps the code, so existing requests stay booked correctly.'}
            </p>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={() => setGroupDraft(null)}>
                Cancel
              </Button>
              <Button size="sm" onClick={saveGroup} loading={saving}>
                Save group
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Booking rules, with the frequent-corrections hint */}
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
          {!ruleDraft && (
            <Button
              size="sm"
              className="shrink-0 whitespace-nowrap"
              onClick={() =>
                setRuleDraft({ index: null, appliesTo: '', groupId: null, maxTotal: '' })
              }
            >
              + Add rule
            </Button>
          )}
        </div>

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
                  → booked under{' '}
                  <span className="font-medium text-ink">
                    {groupLabel(rule.commodity_group_id)}
                  </span>
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
                  setRuleDraft({
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
          {rules.length === 0 && !ruleDraft && (
            <li className="text-sm text-ink/55">No booking rules yet.</li>
          )}
        </ul>

        {ruleDraft && (
          <div className="mt-4 rounded-xl border border-accent/40 bg-accent-soft/10 p-4 space-y-3">
            <p className="text-sm font-medium text-ink">
              {ruleDraft.index === null ? 'New rule' : 'Edit rule'}
            </p>
            <Input
              label="Purchases of…"
              value={ruleDraft.appliesTo}
              onChange={(e) => setRuleDraft({ ...ruleDraft, appliesTo: e.target.value })}
              placeholder="e.g. printer toner"
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                …are booked under
              </label>
              <select
                value={ruleDraft.groupId ?? ''}
                onChange={(e) =>
                  setRuleDraft({
                    ...ruleDraft,
                    groupId: e.target.value ? Number(e.target.value) : null,
                  })
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900"
              >
                <option value="">Select a commodity group</option>
                {groups?.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.id} · {group.category} · {group.name}
                  </option>
                ))}
              </select>
            </div>
            <Input
              label="Only up to ($ per request, optional)"
              type="number"
              step="0.01"
              value={ruleDraft.maxTotal}
              onChange={(e) => setRuleDraft({ ...ruleDraft, maxTotal: e.target.value })}
              placeholder="e.g. 500 for “small” purchases"
            />
            <div className="flex gap-2 justify-end">
              <Button variant="outline" size="sm" onClick={() => setRuleDraft(null)}>
                Cancel
              </Button>
              <Button size="sm" onClick={saveRule} loading={saving}>
                Save rule
              </Button>
            </div>
          </div>
        )}

        {/* Frequent corrections: a hint that a rule may be missing */}
        <div className="mt-6 border-t border-line pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-base font-semibold text-ink">Frequent corrections</h3>
            <label className="flex items-center gap-2 text-sm text-ink/70">
              Show after
              <input
                type="number"
                min={1}
                max={100}
                value={threshold}
                onChange={(e) => setThreshold(Math.max(1, parseInt(e.target.value) || 1))}
                onBlur={() => saveSettings({ correction_alert_threshold: threshold })}
                className="w-16 px-2 py-1 border border-gray-300 rounded-lg text-gray-900"
              />
              corrections
            </label>
          </div>
          <p className="text-sm text-ink/55 mt-1">
            When requesters keep replacing the suggested group the same way, a rule may be
            missing.
          </p>
          <ul className="mt-3 space-y-2">
            {corrections.map((c) => (
              <li
                key={`${c.suggested.id}-${c.chosen.id}`}
                className="flex items-center gap-4 rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-ink">
                    Suggested <span className="font-medium">{c.suggested.name}</span> →
                    requesters chose <span className="font-medium">{c.chosen.name}</span>
                    <span className="ml-2 text-ink/55">{c.count}×</span>
                  </p>
                  <p className="text-xs text-ink/55 mt-0.5 truncate">
                    e.g. {c.examples.map((t) => `“${t}”`).join(', ')}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0 whitespace-nowrap"
                  onClick={() =>
                    setRuleDraft({ index: null, appliesTo: '', groupId: c.chosen.id, maxTotal: '' })
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
      </div>
    </>
  );
}

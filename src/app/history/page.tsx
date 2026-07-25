'use client';
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { useStore } from '@/store/useStore';
import { History, ShieldAlert, ChevronDown, ChevronUp, Clock, FileText, ExternalLink, Scale, CheckSquare } from 'lucide-react';
import Link from 'next/link';

interface Conversation {
  id: number;
  incident: string;
  response_json: string;
  is_lawyer_mode: boolean;
  created_at: string;
}

interface HistoryLawSource {
  law_name: string;
  exact_verbatim_citation?: string;
}

interface QueryResult {
  sources?: Array<{
    act: string;
    section: string;
    text: string;
    confidence_score?: number;
  }>;
  applicable_laws?: Array<HistoryLawSource>;
}

export default function HistoryPage() {
  const router = useRouter();
  const { user, isLoading: authLoading } = useAuth();
  const { setQuery, setAnalysisResult, setIsLawyerMode } = useStore();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    if (authLoading) return;
    
    // Redirect if not logged in
    if (!user) {
      router.push('/login');
      return;
    }

    const fetchHistory = async () => {
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:7861';
      try {
        const reqHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
        if (user && user.token) {
          reqHeaders['Authorization'] = `Bearer ${user.token}`;
        }
        const response = await fetch(`${API_BASE}/api/history`, {
          method: 'POST',
          headers: reqHeaders,
          body: JSON.stringify({ user_id: user.user_id }),
        });
        const data = await response.json();
        if (response.ok && data.success) {
          setConversations(data.conversations);
        } else {
          setError(data.error || 'Failed to fetch history.');
        }
      } catch (e: unknown) {
        const err = e as Error;
        setError(err.message || 'Error occurred while loading history.');
      } finally {
        setLoading(false);
      }
    };

    fetchHistory();
  }, [user, authLoading, router]);

  const handleViewFullReport = (conv: Conversation, resultData: QueryResult) => {
    if (!resultData) return;

    // Standardize structure so analysis page maps it properly
    let sources = resultData.sources || [];
    if (sources.length === 0 && resultData.applicable_laws) {
      sources = resultData.applicable_laws.map((law: HistoryLawSource) => {
        const parts = law.law_name.split('Section');
        const act = parts[0] ? parts[0].replace(/,$/, '').trim() : 'BNS';
        const section = parts[1] ? parts[1].trim() : law.law_name;
        return {
          filename: act,
          page: 0,
          section: section,
          act: act,
          text: law.exact_verbatim_citation || ''
        };
      });
    }

    // Set store state and navigate
    setQuery(conv.incident);
    setAnalysisResult({
      ...resultData,
      sources
    });
    setIsLawyerMode(conv.is_lawyer_mode);
    router.push('/analysis');
  };

  if (authLoading || loading) {
    return (
      <div className="py-8 space-y-4 max-w-4xl mx-auto animate-pulse">
        <div className="h-8 w-44 bg-gray-900 rounded" />
        <div className="h-20 bg-[#0b0b0b] rounded border border-wine/10" />
        <div className="h-20 bg-[#0b0b0b] rounded border border-wine/10" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center max-w-md mx-auto space-y-4">
        <ShieldAlert className="w-10 h-10 text-wine" />
        <h2 className="font-serif text-2xl font-bold text-white">Error Loading History</h2>
        <p className="text-gray-400 text-xs font-mono">{error}</p>
      </div>
    );
  }

  return (
    <div className="py-8 max-w-4xl mx-auto space-y-8 px-4 sm:px-0">
      <div className="flex items-center space-x-2.5 border-b border-wine/10 pb-4">
        <History className="w-6 h-6 text-wine" />
        <h1 className="font-serif text-3xl font-bold text-white">Your Query History</h1>
      </div>

      {conversations.length === 0 ? (
        <div className="text-center py-16 border border-wine/25 border-dashed rounded p-8">
          <FileText className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400 text-sm">No analysis history logged yet.</p>
          <Link
            href="/"
            className="mt-4 inline-block bg-wine hover:bg-wine-dark text-white px-4 py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold transition duration-200"
          >
            Start New Query
          </Link>
        </div>
      ) : (
        <div className="space-y-4">
          {conversations.map((conv) => {
            const isExpanded = expandedId === conv.id;
            let resultData: QueryResult | null = null;
            try {
              resultData = JSON.parse(conv.response_json) as QueryResult;
            } catch {
              // empty
            }

            const applicableLaws = resultData?.applicable_laws || [];

            return (
              <div
                key={conv.id}
                className="bg-[#0b0b0b] border border-wine/20 rounded overflow-hidden transition duration-300 hover:border-wine/30"
              >
                {/* Header card */}
                <div
                  onClick={() => setExpandedId(isExpanded ? null : conv.id)}
                  className="p-5 flex items-center justify-between cursor-pointer select-none"
                >
                  <div className="space-y-1 pr-4">
                    <p className="text-gray-200 font-serif text-base font-semibold line-clamp-1">
                      &ldquo;{conv.incident}&rdquo;
                    </p>
                    <div className="flex items-center space-x-4 text-xs text-gray-500 font-mono">
                      <span className="flex items-center space-x-1">
                        <Clock className="w-3.5 h-3.5" />
                        <span>{new Date(conv.created_at).toLocaleDateString()}</span>
                      </span>
                      <span>Mode: {conv.is_lawyer_mode ? 'Lawyer' : 'Citizen'}</span>
                    </div>
                  </div>
                  <div className="text-wine">
                    {isExpanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
                  </div>
                </div>

                {/* Body Details (Expandable) */}
                {isExpanded && (
                  <div className="border-t border-wine/10 bg-[#050505] p-5 space-y-5 text-sm text-gray-300">
                    {/* Header info / Meta details */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-wine/10 pb-3">
                      <div>
                        <span className="font-semibold text-[10px] uppercase font-mono tracking-wider text-gray-500 block">
                          Original Query
                        </span>
                        <p className="text-gray-200 font-serif text-base italic leading-relaxed">
                          &ldquo;{conv.incident}&rdquo;
                        </p>
                      </div>
                      <button
                        onClick={() => resultData && handleViewFullReport(conv, resultData)}
                        className="flex items-center justify-center space-x-1.5 bg-wine hover:bg-wine-dark text-white px-4 py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold transition duration-200 shrink-0 self-start sm:self-center cursor-pointer"
                      >
                        <span>See Full Response</span>
                        <ExternalLink className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {/* Display applicable laws list */}
                    <div className="space-y-4">
                      <span className="font-semibold text-xs uppercase font-mono tracking-wider text-wine block flex items-center space-x-1.5">
                        <Scale className="w-4 h-4 text-wine" />
                        <span>Applicable Laws & Provisions</span>
                      </span>

                      {applicableLaws.length > 0 ? (
                        <div className="grid grid-cols-1 gap-4">
                          {applicableLaws.map((law: HistoryLawSource, idx: number) => (
                            <div key={idx} className="bg-black border border-wine/10 p-4 rounded space-y-3">
                              <div className="flex items-center justify-between border-b border-wine/5 pb-2">
                                <p className="font-serif text-sm font-bold text-white">
                                  {law.law_name}
                                </p>
                                {law.confidence_score && (
                                  <span className="text-[10px] font-mono bg-wine/10 border border-wine/30 text-wine px-2 py-0.5 rounded">
                                    Confidence: {Math.round(law.confidence_score * 100)}%
                                  </span>
                                )}
                              </div>
                              
                              {law.exact_verbatim_citation && (
                                <p className="text-xs text-gray-400 italic leading-relaxed pl-3 border-l-2 border-wine/30 py-1">
                                  &ldquo;{law.exact_verbatim_citation.trim()}&rdquo;
                                </p>
                              )}

                              {law.plain_english_reason && (
                                <p className="text-xs text-gray-300">
                                  <span className="font-bold text-gray-500 font-mono text-[10px] uppercase block mb-0.5">Reasoning</span>
                                  {law.plain_english_reason}
                                </p>
                              )}

                              {law.evidence_required && law.evidence_required.length > 0 && (
                                <div className="space-y-1.5 pt-1">
                                  <span className="font-bold text-gray-500 font-mono text-[10px] uppercase flex items-center space-x-1">
                                    <CheckSquare className="w-3 h-3 text-wine" />
                                    <span>Evidence Needed to Prove</span>
                                  </span>
                                  <ul className="list-disc list-inside text-xs text-gray-400 pl-1 space-y-1">
                                    {law.evidence_required.map((item: string, eIdx: number) => (
                                      <li key={eIdx}>{item}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="text-xs text-gray-500 italic">No specific law provisions annotated for this query.</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

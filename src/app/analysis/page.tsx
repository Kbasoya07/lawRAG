'use client';

import React, { useEffect, useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useStore } from '@/store/useStore';
import { useAuth } from '@/context/AuthContext';
import { LawCard } from '@/components/LawCard';
import { JudgmentCard } from '@/components/JudgmentCard';
import { FirstStepsCard } from '@/components/FirstStepsCard';
import { DisclaimerBanner } from '@/components/DisclaimerBanner';
import { ShieldAlert, ArrowLeft, RotateCcw } from 'lucide-react';
import Link from 'next/link';

interface LawAnalysis {
  law_name: string;
  confidence_score: number;
  verbatim_text?: string;
  exact_verbatim_citation?: string;
  how_it_applies_to_facts?: string;
  plain_english_explanation?: string;
  plain_english_reason?: string;
  evidence_required?: string[];
  first_steps_citizen?: string[];
  first_steps_lawyer?: string[];
}

interface Source {
  act: string;
  section: string;
  confidence_score?: number;
  text: string;
}

interface ParsedLaw {
  id: number;
  law_name: string;
  confidence_score: number;
  exact_verbatim_citation: string;
  plain_english_reason: string;
  evidence_needed: string[];
}

interface Judgment {
  case_title: string;
  court: string;
  hearing_date: string;
  verdict: string;
  similarity_score: number;
  court_observation: string;
  source_url: string;
}

export default function AnalysisPage() {
  const router = useRouter();
  const { query, analysisResult, setAnalysisResult, isLawyerMode } = useStore();
  const { user, refreshCredits } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetchInitiated = useRef(false);

  useEffect(() => {
    // If no query is present, redirect to home
    if (!query || !query.trim()) {
      router.push('/');
      return;
    }

    if (analysisResult) {
      setLoading(false);
      return;
    }

    if (fetchInitiated.current) return;
    fetchInitiated.current = true;

    const runAnalysis = async () => {
      setLoading(true);
      setError(null);
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:7861';
      try {
        const reqHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
        if (user && user.token) {
          reqHeaders['Authorization'] = `Bearer ${user.token}`;
        }
        const response = await fetch(`${API_BASE}/api/query`, {
          method: 'POST',
          headers: reqHeaders,
          body: JSON.stringify({
            query: query,
            is_lawyer_mode: isLawyerMode,
            user_id: user?.user_id || '',
          }),
        });

        const data = await response.json();
        
        if (!response.ok || data.status === 'INSUFFICIENT_CREDITS') {
          if (data.status === 'INSUFFICIENT_CREDITS' || data.error === 'Credit limit reached. Please sign up or purchase more credits.') {
            setError('INSUFFICIENT_CREDITS');
          } else {
            setError(data.error || data.detail || 'Failed to complete analysis.');
          }
          setLoading(false);
          return;
        }

        setAnalysisResult(data);
        
        // Refresh credits balance if logged in
        if (user) {
          refreshCredits();
        }
      } catch (e: unknown) {
        const err = e as Error;
        setError(err.message || 'An error occurred during query execution.');
      } finally {
        setLoading(false);
      }
    };

    runAnalysis();
  }, [query, analysisResult, isLawyerMode, refreshCredits, router, setAnalysisResult, user]);

  // Loading layout — Law Sections Connecting animation
  if (loading) {
    // Node positions arranged in a circle of radius 115 around center (160,160)
    // Angles: 270°(top), 330°, 30°, 90°(bottom), 150°, 210°
    const nodes = [
      { label: 'BNS',    cx: 160,  cy: 45  },   // top
      { label: 'BNSS',   cx: 259,  cy: 102 },   // top-right
      { label: 'IPC',    cx: 259,  cy: 218 },   // bottom-right
      { label: 'BSA',    cx: 160,  cy: 275 },   // bottom
      { label: 'IT Act', cx: 61,   cy: 218 },   // bottom-left
      { label: 'CrPC',   cx: 61,   cy: 102 },   // top-left
    ];

    return (
      <div className="flex flex-col items-center justify-center min-h-[65vh] text-center px-4">
        <style>{`
          @keyframes centerPulse {
            0%, 100% { box-shadow: 0 0 12px 3px rgba(107,29,47,0.45), 0 0 28px 6px rgba(107,29,47,0.2); }
            50%       { box-shadow: 0 0 24px 8px rgba(107,29,47,0.8), 0 0 52px 14px rgba(107,29,47,0.35); }
          }
          @keyframes nodeGlow {
            0%, 100% { box-shadow: 0 0 4px 1px rgba(107,29,47,0.2); opacity: 0.55; }
            50%       { box-shadow: 0 0 12px 4px rgba(107,29,47,0.7); opacity: 1; }
          }
          @keyframes flowDash {
            0%   { stroke-dashoffset: 120; opacity: 0.15; }
            45%  { stroke-dashoffset: 0;   opacity: 1;    }
            100% { stroke-dashoffset: 0;   opacity: 0.15; }
          }
          .node-n0 { animation: nodeGlow 3s ease-in-out infinite 0.0s; }
          .node-n1 { animation: nodeGlow 3s ease-in-out infinite 0.5s; }
          .node-n2 { animation: nodeGlow 3s ease-in-out infinite 1.0s; }
          .node-n3 { animation: nodeGlow 3s ease-in-out infinite 1.5s; }
          .node-n4 { animation: nodeGlow 3s ease-in-out infinite 2.0s; }
          .node-n5 { animation: nodeGlow 3s ease-in-out infinite 2.5s; }
          .line-n0 { animation: flowDash 3s ease-in-out infinite 0.0s; }
          .line-n1 { animation: flowDash 3s ease-in-out infinite 0.5s; }
          .line-n2 { animation: flowDash 3s ease-in-out infinite 1.0s; }
          .line-n3 { animation: flowDash 3s ease-in-out infinite 1.5s; }
          .line-n4 { animation: flowDash 3s ease-in-out infinite 2.0s; }
          .line-n5 { animation: flowDash 3s ease-in-out infinite 2.5s; }
          .center-glow { animation: centerPulse 2s ease-in-out infinite; }
        `}</style>

        {/* Network canvas */}
        <div className="relative" style={{ width: 320, height: 320 }}>

          {/* SVG layer — lines from each outer node to center */}
          <svg
            className="absolute inset-0 pointer-events-none"
            width="320" height="320" viewBox="0 0 320 320"
          >
            {nodes.map((n, i) => (
              <line
                key={i}
                className={`line-n${i}`}
                x1={n.cx} y1={n.cy}
                x2="160"  y2="160"
                stroke="#6b1d2f"
                strokeWidth="1.5"
                strokeDasharray="120"
                strokeLinecap="round"
              />
            ))}
          </svg>

          {/* Center "Your Case" node */}
          <div
            className="center-glow absolute flex flex-col items-center justify-center rounded-full bg-wine/20 border-2 border-wine"
            style={{
              width: 76, height: 76,
              top: 160, left: 160,
              transform: 'translate(-50%, -50%)',
            }}
          >
            <span className="text-wine text-[9px] font-mono font-bold uppercase tracking-widest leading-tight">
              Your<br />Case
            </span>
          </div>

          {/* Outer law nodes */}
          {nodes.map((n, i) => (
            <div
              key={i}
              className={`node-n${i} absolute flex items-center justify-center rounded bg-[#0b0b0b] border border-wine/50`}
              style={{
                top: n.cy, left: n.cx,
                transform: 'translate(-50%, -50%)',
                padding: '4px 8px',
                minWidth: 44,
              }}
            >
              <span className="text-wine/90 font-mono font-semibold uppercase tracking-wider whitespace-nowrap"
                style={{ fontSize: 10 }}>
                {n.label}
              </span>
            </div>
          ))}
        </div>

        {/* Message */}
        <div className="mt-2 space-y-2 max-w-xs">
          <h2 className="font-serif text-xl font-semibold text-white tracking-wide">
            Analyzing Legal Context
          </h2>
          <p className="text-gray-400 text-sm leading-relaxed">
            Generating reliable legal analysis takes a few extra seconds.
            Thank you for your patience as we cross-reference acts and
            double-check citations.
          </p>
        </div>

        <p className="mt-4 text-[10px] uppercase font-mono tracking-widest text-wine/40 animate-pulse">
          Connecting statutes &amp; precedents...
        </p>
      </div>
    );
  }

  // Credit limit reached error state
  if (error === 'INSUFFICIENT_CREDITS') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center max-w-md mx-auto space-y-6">
        <div className="bg-[#0b0b0b] border border-wine/30 rounded-full p-4 text-wine">
          <ShieldAlert className="w-10 h-10" />
        </div>
        <h2 className="font-serif text-2xl font-bold text-white">Credit Limit Reached</h2>
        <p className="text-gray-400 text-sm leading-relaxed">
          You have exhausted your daily free query limits (for public tier) or your account balance (for logged in users). 
          {!user && " Register a free account to receive 10 signup credits instantly."}
        </p>
        <div className="flex flex-col sm:flex-row gap-4 w-full">
          {!user ? (
            <Link
              href="/signup"
              className="flex-1 bg-wine text-white py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold hover:bg-wine-dark transition duration-200"
            >
              Sign Up (10 Credits)
            </Link>
          ) : (
            <button className="flex-1 bg-wine text-white py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold hover:bg-wine-dark transition duration-200">
              Purchase More Credits
            </button>
          )}
          <Link
            href="/"
            className="flex-1 border border-gray-800 text-gray-400 py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold hover:text-white transition duration-200"
          >
            Back to Home
          </Link>
        </div>
      </div>
    );
  }

  // General pipeline exceptions or data failure
  if (error || !analysisResult) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center max-w-md mx-auto space-y-6">
        <div className="bg-[#0b0b0b] border border-wine/30 rounded-full p-4 text-wine">
          <ShieldAlert className="w-10 h-10" />
        </div>
        <h2 className="font-serif text-2xl font-bold text-white">Analysis Execution Error</h2>
        <p className="text-gray-400 text-xs font-mono">{error || 'Unknown analysis error'}</p>
        <Link
          href="/"
          className="bg-wine text-white px-6 py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold hover:bg-wine-dark transition duration-200"
        >
          Try Again
        </Link>
      </div>
    );
  }

  const { status, recommended_actions, similar_judgments, sources } = analysisResult;

  // Handle INSUFFICIENT_DATA gracefully
  if (status === 'INSUFFICIENT_DATA') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center max-w-lg mx-auto space-y-6">
        <div className="bg-[#0b0b0b] border border-wine/30 rounded-full p-4 text-wine">
          <ShieldAlert className="w-10 h-10" />
        </div>
        <h2 className="font-serif text-2xl font-bold text-white">Insufficient Legal Data</h2>
        <p className="text-gray-400 text-sm leading-relaxed">
          I could not locate sufficient matching legal provisions in the database for your specific scenario with high confidence. 
          Please refine your description to include more details.
        </p>
        <div className="flex gap-4">
          <Link
            href="/"
            className="bg-wine text-white px-6 py-2.5 rounded text-xs uppercase font-mono tracking-wider font-semibold hover:bg-wine-dark transition duration-200 flex items-center space-x-1.5"
          >
            <RotateCcw className="w-4 h-4" />
            <span>Refine Query</span>
          </Link>
        </div>
      </div>
    );
  }

  // Parse sections to populate LawCards
  const parsedLaws = (analysisResult.applicable_laws && analysisResult.applicable_laws.length > 0)
    ? analysisResult.applicable_laws.map((law: LawAnalysis, idx: number) => ({
        id: idx,
        law_name: law.law_name,
        confidence_score: law.confidence_score,
        exact_verbatim_citation: law.verbatim_text || law.exact_verbatim_citation || '',
        plain_english_reason: law.how_it_applies_to_facts || law.plain_english_explanation || law.plain_english_reason || '',
        evidence_needed: law.evidence_required || []
      }))
    : (sources || []).map((src: Source, idx: number) => ({
        id: idx,
        law_name: `${src.act}, Section ${src.section}`,
        confidence_score: src.confidence_score || 0.65,
        exact_verbatim_citation: src.text,
        plain_english_reason: `This provision applies directly as the elements of the described incident correspond to the legal definitions outlined in ${src.act} Section ${src.section}.`,
        evidence_needed: ['Standard incident/police report', 'Verified factual timeline matching section definitions']
      }));

  // Synthesize citizen & lawyer first steps
  let citizenSteps = (recommended_actions || []).filter((a: string) => !a.toLowerCase().includes('u/s') && !a.toLowerCase().includes('crpc'));
  let lawyerSteps = (recommended_actions || []).filter((a: string) => a.toLowerCase().includes('u/s') || a.toLowerCase().includes('crpc') || a.toLowerCase().includes('petition'));

  if (citizenSteps.length === 0 && lawyerSteps.length === 0 && analysisResult.applicable_laws) {
    const aggCitizen = new Set<string>();
    const aggLawyer = new Set<string>();
    analysisResult.applicable_laws.forEach((law: LawAnalysis) => {
      (law.first_steps_citizen || []).forEach((s: string) => aggCitizen.add(s));
      (law.first_steps_lawyer || []).forEach((s: string) => aggLawyer.add(s));
    });
    citizenSteps = Array.from(aggCitizen);
    lawyerSteps = Array.from(aggLawyer);
  }

  // Fill fallbacks if list partitioning is empty
  const finalCitizenSteps = citizenSteps.length > 0 ? citizenSteps : ["No specific citizen action steps defined in matching laws."];
  const finalLawyerSteps = lawyerSteps.length > 0 ? lawyerSteps : ["No specific legal procedural steps defined in matching laws."];

  return (
    <div className="pb-24 space-y-10 max-w-5xl mx-auto">
      {/* Top action header */}
      <div className="flex items-center justify-between border-b border-wine/10 pb-4">
        <Link
          href="/"
          className="flex items-center space-x-1 text-xs uppercase font-mono tracking-wider font-semibold text-wine hover:text-white transition duration-200"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>New Query</span>
        </Link>
        <span className="text-xs text-gray-500 font-mono">CONFIDENTIAL ANALYSIS</span>
      </div>

      {/* Query Detail Title */}
      <div className="space-y-2">
        <h1 className="font-serif text-3xl md:text-4xl font-bold text-white leading-tight">
          Legal Analysis Report
        </h1>
        <p className="text-gray-400 text-sm leading-relaxed border-l-2 border-wine/50 pl-3 max-w-3xl">
          <span className="font-semibold text-gray-300">Incident Query:</span> &ldquo;{query}&rdquo;
        </p>
      </div>

      {/* Degraded-mode banner — shown only when LLM is rate-limited */}
      {analysisResult.llm_unavailable && (
        <div className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
          <span className="text-lg leading-none">⚡</span>
          <div>
            <span className="font-semibold">Quick Results Mode</span> — AI synthesis is temporarily unavailable (LLM rate limits). Showing retrieval-only results from the legal database. Full AI analysis resumes at <span className="font-mono font-semibold">5:30 AM IST</span>.
          </div>
        </div>
      )}

      {/* Embedding fallback banner — shown when Voyage AI is rate-limited */}
      {analysisResult.embedding_notice && (
        <div className="flex items-start gap-3 rounded-lg border border-orange-500/30 bg-orange-500/10 px-4 py-3 text-sm text-orange-300">
          <span className="text-lg leading-none">🔄</span>
          <div>
            <span className="font-semibold">Local Fallback Mode</span> — Primary embedding API (Voyage AI) is currently rate-limited (free tier: 3 req/min). Using local BGE model as fallback. Results may have slightly reduced accuracy. To restore full accuracy, add a payment method at <a href="https://dashboard.voyageai.com" target="_blank" rel="noopener noreferrer" className="underline hover:text-orange-200">dashboard.voyageai.com</a>.
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Left Column: Laws & Explanations */}
        <div className="lg:col-span-2 space-y-6">
          <h2 className="text-sm md:text-base uppercase font-mono font-bold tracking-widest text-wine border-b border-wine/10 pb-2">
            Applicable Laws & Provisions
          </h2>

          <div className="space-y-6">
            {parsedLaws.map((law: ParsedLaw) => (
              <LawCard
                key={law.id}
                sectionName={law.law_name}
                confidence={law.confidence_score}
                reasoning={law.plain_english_reason}
                verbatimCitation={law.exact_verbatim_citation}
                evidenceNeeded={law.evidence_needed}
              />
            ))}
          </div>
        </div>

        {/* Right Column: First Steps & Action Plan */}
        <div className="space-y-6">
          <h2 className="text-sm md:text-base uppercase font-mono font-bold tracking-widest text-wine border-b border-wine/10 pb-2">
            Procedural Action Plan
          </h2>
          <FirstStepsCard citizenSteps={finalCitizenSteps} lawyerSteps={finalLawyerSteps} />
        </div>
      </div>

      {/* Similar Case Precedents */}
      {similar_judgments && similar_judgments.length > 0 && (
        <div className="space-y-6 pt-6 border-t border-wine/10">
          <h2 className="text-sm md:text-base uppercase font-mono font-bold tracking-widest text-wine border-b border-wine/10 pb-2">
            Relevant Case Precedents
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {similar_judgments.map((caseItem: Judgment, idx: number) => (
              <JudgmentCard
                key={idx}
                caseTitle={caseItem.case_title}
                court={caseItem.court}
                hearingDate={caseItem.hearing_date}
                verdict={caseItem.verdict}
                similarityScore={caseItem.similarity_score}
                observation={caseItem.court_observation}
                sourceUrl={caseItem.source_url}
              />
            ))}
          </div>
        </div>
      )}

      {/* Sticky Bottom Disclaimer and advocate connecting binary */}
      <DisclaimerBanner />
    </div>
  );
}

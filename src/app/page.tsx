'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { useStore } from '@/store/useStore';
import { useAuth } from '@/context/AuthContext';
import { ShieldAlert, ArrowRight } from 'lucide-react';
import Link from 'next/link';

export default function LandingPage() {
  const router = useRouter();
  const { query, setQuery, setIsLawyerMode, defaultLawyerMode, setAnalysisResult } = useStore();
  const { user, credits, refreshCredits } = useAuth();

  React.useEffect(() => {
    // Clear any previous analysis when returning to the landing page
    setAnalysisResult(null);
    if (user) {
      refreshCredits();
    }
  }, [setAnalysisResult, user, refreshCredits]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    // Set mode according to stored default preference
    setIsLawyerMode(defaultLawyerMode);
    
    // Redirect to analysis page to execute the pipeline
    router.push('/analysis');
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[75vh] py-12">
      {/* Hero Section */}
      <div className="text-center max-w-3xl space-y-4 mb-10">
        <h1 className="font-serif text-5xl md:text-6xl font-bold tracking-tight text-white leading-tight">
          <span className="text-wine">Nyaya Dayak</span> is here to help you
        </h1>
        <p className="text-gray-400 text-sm md:text-base tracking-wide max-w-xl mx-auto font-medium">
          Tell us about your situation.
        </p>
      </div>

      {/* Input query container */}
      <form onSubmit={handleSubmit} className="w-full max-w-2xl flex flex-col space-y-6 items-center">
        <div className="w-full rounded bg-[#0b0b0b] p-6 glow-border-wine">
          <label htmlFor="incident" className="sr-only">
            Describe your legal situation
          </label>
          <textarea
            id="incident"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Describe your legal situation, context, and questions here in detail..."
            className="w-full h-44 bg-black border-none text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-0 resize-none font-serif text-base leading-relaxed"
          />
        </div>

        {/* Submit Action */}
        <button
          type="submit"
          className="flex items-center space-x-2 border border-wine px-8 py-3 rounded text-sm uppercase font-mono tracking-wider font-semibold text-wine hover:text-white hover:bg-wine hover:shadow-[0_0_15px_rgba(107,29,47,0.4)] transition duration-300 cursor-pointer"
        >
          <span>Analyze Case</span>
          <ArrowRight className="w-4 h-4" />
        </button>
      </form>

      {/* Anonymous Banner Promo */}
      {!user && (
        <div className="mt-12 w-full max-w-md bg-[#050505] border border-wine/20 rounded p-4 flex items-start space-x-3">
          <ShieldAlert className="w-5 h-5 text-wine flex-shrink-0 mt-0.5" />
          <div className="text-xs text-gray-400 leading-relaxed">
            <span className="font-bold text-white uppercase tracking-wider block mb-1">Public Tier</span>
            Your conversations are not saved. 
            <Link href="/signup" className="text-wine font-semibold hover:text-white underline ml-1">
              Sign up
            </Link>{' '}
            for history tracking and 10 free credits.
          </div>
        </div>
      )}

      {/* Authenticated User Status Panel */}
      {user && (
        <div className="mt-12 w-full max-w-md bg-[#050505] border border-wine/20 rounded p-4 flex items-start space-x-3">
          <div className="text-xs text-gray-400 leading-relaxed w-full">
            <span className="font-bold text-white uppercase tracking-wider block mb-1">User Profile Status</span>
            Logged in as: <span className="text-white font-mono">{user.email}</span>
            <div className="mt-2 flex items-center justify-between border-t border-wine/10 pt-2">
              <span>Remaining Balance:</span>
              <span className="text-wine font-bold font-mono text-sm">{credits !== null ? `${credits} credits` : 'Loading...'}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

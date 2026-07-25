'use client';

import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import Link from 'next/link';
import { useAuth } from '@/context/AuthContext';
import { useStore } from '@/store/useStore';
import { Scale, History, LogIn, LogOut, UserPlus, Check, MessageSquare, X } from 'lucide-react';

const CitizenAvatar: React.FC<{ className?: string }> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 100 100" className={`${className} rounded-full border border-wine/30 bg-[#121212] transition-colors duration-300`}>
    <circle cx="50" cy="50" r="48" fill="#18181b" />
    <circle cx="50" cy="35" r="18" fill="#71717a" />
    <path d="M18 78c0-18 12-25 32-25s32 7 32 25z" fill="#71717a" />
  </svg>
);

const LawyerAvatar: React.FC<{ className?: string }> = ({ className = "w-8 h-8" }) => (
  <svg viewBox="0 0 100 100" className={`${className} rounded-full border border-wine/30 bg-[#0f172a] transition-colors duration-300`}>
    <circle cx="50" cy="50" r="48" fill="#090d16" />
    <circle cx="50" cy="35" r="17" fill="#94a3b8" />
    <path d="M18 78c0-18 12-25 32-25s32 7 32 25z" fill="#1e293b" />
    <path d="M42 53 L50 64 L58 53 Z" fill="#ffffff" />
    <path d="M35 53 L50 62 L32 78 Z" fill="#090d16" />
    <path d="M65 53 L50 62 L68 78 Z" fill="#090d16" />
    <path d="M45.5 59 h4 v15 h-4 z" fill="#ffffff" />
    <path d="M50.5 59 h4 v15 h-4 z" fill="#ffffff" />
    <rect x="42.5" y="58" width="15" height="2" fill="#ffffff" />
  </svg>
);

export const Navbar: React.FC = () => {
  const { user, credits, logout } = useAuth();
  const { isLawyerMode, setIsLawyerMode, defaultLawyerMode, setDefaultLawyerMode } = useStore();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isCreditAlert = credits !== null && credits < 3;

  // Sync mount state
  useEffect(() => {
    setMounted(true);
  }, []);

  // Click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <nav className="w-full bg-black/80 border-b border-wine/30 sticky top-0 z-50 backdrop-blur-md px-4 md:px-6 py-3 md:py-4 flex items-center justify-between transition-colors duration-300">
      <Link href="/" className="flex items-center space-x-1.5 md:space-x-2 flex-shrink-0">
        <Scale className="w-5 h-5 md:w-6 h-6 text-wine transition-colors duration-300" />
        <span className="font-serif text-sm sm:text-2xl font-bold tracking-wide text-white">
          Nyaya Dayak
        </span>
      </Link>

      <div className="flex items-center space-x-2.5 sm:space-x-4 md:space-x-6">
        {/* Desktop-only Direct Links */}
        <Link href="/" className="text-gray-300 hover:text-white transition duration-200 text-xs md:text-sm hidden sm:inline">
          <span>New Analysis</span>
        </Link>

        {mounted && user && (
          <Link
            href="/history"
            className="text-gray-300 hover:text-white transition duration-200 text-xs md:text-sm flex items-center space-x-1"
            aria-label="History logs"
          >
            <History className="w-4 h-4" />
            <span className="hidden sm:inline">History</span>
          </Link>
        )}

        <button
          onClick={() => setFeedbackOpen(true)}
          className="text-gray-300 hover:text-white transition duration-200 text-xs md:text-sm cursor-pointer flex items-center space-x-1"
          aria-label="Give feedback"
        >
          <MessageSquare className="w-4 h-4" />
          <span className="hidden sm:inline">give your feedback :)</span>
        </button>

        {mounted && credits !== null && (
          <div className="flex items-center space-x-1.5 bg-[#0a0a0a] px-2.5 py-1 rounded border border-wine/30 transition-colors duration-300">
            <span className="text-[10px] text-gray-400 font-medium hidden sm:inline">
              {user ? 'Credits:' : 'Free Daily:'}
            </span>
            <span
              className={`font-mono text-xs font-bold transition-colors duration-300 ${
                isCreditAlert ? 'text-red-500 animate-pulse' : 'text-wine'
              }`}
            >
              {credits}
            </span>
          </div>
        )}

        {/* Profile Avatar Dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="focus:outline-none cursor-pointer flex items-center justify-center p-0.5 rounded-full hover:ring-2 hover:ring-wine/50 transition-all duration-300"
            aria-label="User Profile menu"
          >
            {mounted ? (
              isLawyerMode ? <LawyerAvatar className="w-8 h-8 md:w-9 h-9" /> : <CitizenAvatar className="w-8 h-8 md:w-9 h-9" />
            ) : (
              <div className="w-8 h-8 md:w-9 h-9 rounded-full bg-gray-800 animate-pulse" />
            )}
          </button>

          {mounted && dropdownOpen && (
            <div className="absolute right-0 mt-3 w-72 bg-[#070707] border border-wine/30 rounded shadow-2xl py-3 z-50 animate-in fade-in slide-in-from-top-2 duration-200">
              {/* User Identity Header */}
              <div className="px-4 py-2 border-b border-wine/10 mb-2">
                <p className="text-xs text-gray-500 font-mono">ACCOUNT PROFILE</p>
                <p className="text-sm font-semibold text-white truncate">
                  {user ? user.email : 'Guest User'}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">
                  {isLawyerMode ? 'Active: Advocate Console' : 'Active: Citizen Advisor'}
                </p>
              </div>

              {/* Mobile Navigation options inside Dropdown */}
              <div className="px-4 py-2 border-b border-wine/10 sm:hidden space-y-2">
                <p className="text-xs font-mono text-gray-500 uppercase">Navigation</p>
                <div className="flex flex-col space-y-1.5">
                  <Link
                    href="/"
                    onClick={() => setDropdownOpen(false)}
                    className="text-xs text-gray-300 hover:text-white flex items-center space-x-2 py-1"
                  >
                    <Scale className="w-3.5 h-3.5 text-wine" />
                    <span>New Analysis</span>
                  </Link>
                  {user && (
                    <Link
                      href="/history"
                      onClick={() => setDropdownOpen(false)}
                      className="text-xs text-gray-300 hover:text-white flex items-center space-x-2 py-1"
                    >
                      <History className="w-3.5 h-3.5 text-wine" />
                      <span>History Logs</span>
                    </Link>
                  )}
                  <button
                    onClick={() => {
                      setDropdownOpen(false);
                      setFeedbackOpen(true);
                    }}
                    className="text-xs text-gray-300 hover:text-white flex items-center space-x-2 py-1 text-left w-full cursor-pointer"
                  >
                    <MessageSquare className="w-3.5 h-3.5 text-wine" />
                    <span>give your feedback :)</span>
                  </button>
                </div>
              </div>

              {/* Mobile Credits Info */}
              {credits !== null && (
                <div className="px-4 py-2 border-b border-wine/10 sm:hidden flex items-center justify-between">
                  <span className="text-xs text-gray-400 font-mono">
                    {user ? 'Credits:' : 'Free Daily:'}
                  </span>
                  <span className={`font-mono text-xs font-bold ${isCreditAlert ? 'text-red-500 animate-pulse' : 'text-wine'}`}>
                    {credits}
                  </span>
                </div>
              )}

              {/* Mode Toggle Selection */}
              <div className="px-4 py-2 space-y-2">
                <p className="text-xs font-mono text-wine tracking-wider uppercase font-bold">Workspace Mode</p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setIsLawyerMode(false)}
                    className={`px-2 py-1.5 text-xs rounded border transition duration-200 text-center font-medium flex items-center justify-center space-x-1 cursor-pointer ${
                      !isLawyerMode
                        ? 'bg-wine text-white border-wine'
                        : 'bg-black text-gray-400 border-gray-800 hover:text-white'
                    }`}
                  >
                    <span>Citizen</span>
                    {!isLawyerMode && <Check className="w-3 h-3 ml-0.5" />}
                  </button>
                  <button
                    onClick={() => setIsLawyerMode(true)}
                    className={`px-2 py-1.5 text-xs rounded border transition duration-200 text-center font-medium flex items-center justify-center space-x-1 cursor-pointer ${
                      isLawyerMode
                        ? 'bg-wine text-white border-wine'
                        : 'bg-black text-gray-400 border-gray-800 hover:text-white'
                    }`}
                  >
                    <span>Lawyer</span>
                    {isLawyerMode && <Check className="w-3 h-3 ml-0.5" />}
                  </button>
                </div>
              </div>

              {/* Default Startup Setting */}
              <div className="px-4 py-2 border-t border-wine/10 mt-2 pt-2">
                <p className="text-xs font-mono text-gray-500 mb-2 uppercase">Default Mode Preference</p>
                <label className="flex items-center space-x-2.5 text-xs text-gray-300 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={!defaultLawyerMode}
                    onChange={(e) => setDefaultLawyerMode(!e.target.checked)}
                    className="accent-wine w-3.5 h-3.5 rounded border-gray-800 bg-black cursor-pointer"
                  />
                  <span>Default to Citizen Mode on load</span>
                </label>
              </div>

              {/* Action Buttons */}
              <div className="border-t border-wine/10 mt-3 pt-2 px-2 space-y-1">
                {user ? (
                  <button
                    onClick={() => {
                      setDropdownOpen(false);
                      logout();
                    }}
                    className="w-full text-left px-3 py-2 rounded text-xs text-red-400 hover:bg-wine/10 hover:text-red-300 transition duration-200 flex items-center space-x-2 cursor-pointer"
                  >
                    <LogOut className="w-3.5 h-3.5" />
                    <span>Sign Out</span>
                  </button>
                ) : (
                  <>
                    <Link
                      href="/login"
                      onClick={() => setDropdownOpen(false)}
                      className="w-full text-left px-3 py-2 rounded text-xs text-gray-300 hover:bg-wine/10 hover:text-white transition duration-200 flex items-center space-x-2"
                    >
                      <LogIn className="w-3.5 h-3.5" />
                      <span>Log In</span>
                    </Link>
                    <Link
                      href="/signup"
                      onClick={() => setDropdownOpen(false)}
                      className="w-full text-left px-3 py-2 rounded text-xs text-gray-300 hover:bg-wine/10 hover:text-white transition duration-200 flex items-center space-x-2"
                    >
                      <UserPlus className="w-3.5 h-3.5" />
                      <span>Create Account</span>
                    </Link>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Feedback Modal via React Portal */}
      {feedbackOpen && mounted && createPortal(
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm flex items-center justify-center z-9999 animate-in fade-in duration-200">
          <div className="bg-[#070707] border border-wine/30 p-6 rounded max-w-sm w-full mx-4 shadow-2xl relative animate-modal-scale-in">
            <button
              onClick={() => setFeedbackOpen(false)}
              className="absolute top-3 right-3 text-gray-400 hover:text-white transition duration-200 cursor-pointer"
              aria-label="Close feedback modal"
            >
              <X className="w-4.5 h-4.5" />
            </button>
            <div className="space-y-4">
              <h3 className="font-serif text-lg font-bold text-white">We&apos;d love your feedback!</h3>
              <p className="text-xs text-gray-400 leading-relaxed">
                Please email us directly with your feedback, suggestions, or issues at:
              </p>
              <div className="bg-black border border-wine/25 p-3 rounded font-mono text-center text-sm text-wine font-semibold select-all">
                kb00001202@gmail.com
              </div>
              <button
                onClick={() => setFeedbackOpen(false)}
                className="w-full bg-wine hover:bg-wine-dark text-white text-xs uppercase font-mono tracking-wider font-semibold py-2 rounded transition duration-200 cursor-pointer"
              >
                Done
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </nav>
  );
};

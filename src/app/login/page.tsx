'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { Scale, Key, Mail, Globe } from 'lucide-react';
import Link from 'next/link';

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError('Please fill in all fields.');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    const success = await login(email, password);
    setIsSubmitting(false);

    if (success) {
      router.push('/');
    } else {
      setError('Invalid email address or incorrect password. Please try again.');
    }
  };

  const handleGoogleOAuth = async () => {
    setError(null);
    setIsSubmitting(true);
    // Simulate google oauth email selection with default pass
    const mockGoogleEmail = 'oauth_user@gmail.com';
    const success = await login(mockGoogleEmail, 'google_oauth_pass');
    setIsSubmitting(false);

    if (success) {
      router.push('/');
    } else {
      setError('Google login failed. Please verify credentials or Sign Up first.');
    }
  };

  return (
    <div className="flex items-center justify-center min-h-[70vh] py-12">
      <div className="w-full max-w-md bg-[#0b0b0b] border border-wine/20 rounded p-8 space-y-6">
        <div className="text-center space-y-2">
          <div className="flex justify-center text-wine">
            <Scale className="w-10 h-10" />
          </div>
          <h2 className="font-serif text-3xl font-bold text-white tracking-wide">
            Welcome Back
          </h2>
          <p className="text-gray-400 text-xs uppercase font-mono tracking-wider">
            Sign in to Nyaya Dayak
          </p>
        </div>

        {error && (
          <div className="bg-rose-950/20 border border-rose-500/30 text-rose-400 p-3 rounded text-xs leading-relaxed">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1">
            <label className="text-xs uppercase font-mono tracking-wider font-semibold text-gray-400">
              Email Address
            </label>
            <div className="flex items-center bg-black border border-wine/20 rounded px-3 py-2 focus-within:border-wine transition duration-200">
              <Mail className="w-4 h-4 text-wine mr-2" />
              <input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="bg-transparent border-none text-gray-200 focus:outline-none focus:ring-0 text-sm w-full"
                required
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs uppercase font-mono tracking-wider font-semibold text-gray-400">
              Password
            </label>
            <div className="flex items-center bg-black border border-wine/20 rounded px-3 py-2 focus-within:border-wine transition duration-200">
              <Key className="w-4 h-4 text-wine mr-2" />
              <input
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="bg-transparent border-none text-gray-200 focus:outline-none focus:ring-0 text-sm w-full"
                required
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={isSubmitting}
            className="w-full bg-wine hover:bg-wine-dark text-white py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold transition duration-200 disabled:opacity-50 cursor-pointer"
          >
            {isSubmitting ? 'Verifying...' : 'Sign In'}
          </button>
        </form>

        <div className="relative flex py-2 items-center">
          <div className="flex-grow border-t border-gray-900"></div>
          <span className="flex-shrink mx-4 text-gray-600 text-xs font-mono uppercase tracking-wider">or</span>
          <div className="flex-grow border-t border-gray-900"></div>
        </div>

        <button
          onClick={handleGoogleOAuth}
          disabled={isSubmitting}
          className="w-full border border-gray-800 hover:border-gray-500 hover:text-white text-gray-300 py-2 rounded text-xs uppercase font-mono tracking-wider font-semibold transition duration-200 flex items-center justify-center space-x-2 cursor-pointer"
        >
          <Globe className="w-4 h-4" />
          <span>Continue with Google</span>
        </button>

        <p className="text-center text-xs text-gray-400">
          Don&apos;t have an account?{' '}
          <Link href="/signup" className="text-wine font-semibold hover:text-white underline">
            Sign Up
          </Link>
        </p>
      </div>
    </div>
  );
}

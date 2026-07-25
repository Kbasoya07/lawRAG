'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

interface User {
  user_id: string;
  email: string;
  token?: string;
}

interface AuthContextType {
  user: User | null;
  credits: number | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<boolean>;
  signup: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
  refreshCredits: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [credits, setCredits] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:7861';

  const fetchCredits = useCallback(async (email?: string, token?: string) => {
    try {
      if (email && token) {
        const response = await fetch(`${API_BASE}/api/user/credits`, {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify({ email }),
        });
        if (response.ok) {
          const data = await response.json();
          setCredits(data.credits);
          return;
        } else if (response.status === 401) {
          console.warn("Session expired. Logging out.");
          setUser(null);
          localStorage.removeItem('nyaya_user');
        }
      }

      // Guest / Anonymous fallback: fetch free daily credits allowance
      const anonRes = await fetch(`${API_BASE}/api/anonymous-credits`);
      if (anonRes.ok) {
        const anonData = await anonRes.json();
        setCredits(anonData.credits);
      } else {
        setCredits(5);
      }
    } catch (error) {
      console.error('Failed to fetch credits:', error);
      setCredits(5);
    }
  }, [API_BASE, setUser]);

  // Load user session on mount
  useEffect(() => {
    const savedUser = localStorage.getItem('nyaya_user');
    if (savedUser) {
      try {
        const parsedUser = JSON.parse(savedUser) as User;
        setUser(parsedUser);
        if (parsedUser.token) {
          fetchCredits(parsedUser.email, parsedUser.token);
        } else {
          fetchCredits();
        }
      } catch {
        localStorage.removeItem('nyaya_user');
        fetchCredits();
      }
    } else {
      fetchCredits();
    }
    setIsLoading(false);
  }, [fetchCredits]);

  const login = async (email: string, password: string): Promise<boolean> => {
    setIsLoading(true);
    try {
      const response = await fetch(`${API_BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (response.ok) {
        const data = await response.json();
        if (data.success && data.token) {
          const loggedUser = { user_id: data.user_id, email, token: data.token };
          setUser(loggedUser);
          setCredits(data.credits);
          localStorage.setItem('nyaya_user', JSON.stringify(loggedUser));
          setIsLoading(false);
          return true;
        }
      }
      setIsLoading(false);
      return false;
    } catch (error) {
      console.error('Login error:', error);
      setIsLoading(false);
      return false;
    }
  };

  const signup = async (email: string, password: string): Promise<boolean> => {
    setIsLoading(true);
    try {
      const user_id = email.split('@')[0] + '_' + Math.floor(Math.random() * 1000);
      const response = await fetch(`${API_BASE}/api/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id, email, password }),
      });
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          return await login(email, password);
        }
      }
      setIsLoading(false);
      return false;
    } catch (error) {
      console.error('Signup error:', error);
      setIsLoading(false);
      return false;
    }
  };

  const logout = () => {
    setUser(null);
    localStorage.removeItem('nyaya_user');
    fetchCredits();
  };

  const refreshCredits = async () => {
    if (user && user.token) {
      await fetchCredits(user.email, user.token);
    } else {
      await fetchCredits();
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        credits,
        isLoading,
        login,
        signup,
        logout,
        refreshCredits,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

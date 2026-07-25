import { create } from 'zustand';

interface QueryState {
  query: string;
  setQuery: (query: string) => void;
  isLawyerMode: boolean;
  setIsLawyerMode: (isLawyerMode: boolean) => void;
  defaultLawyerMode: boolean;
  setDefaultLawyerMode: (defaultLawyerMode: boolean) => void;
  analysisResult: unknown;
  setAnalysisResult: (result: unknown) => void;
  isLoading: boolean;
  setIsLoading: (isLoading: boolean) => void;
}

const getInitialLawyerMode = (): boolean => {
  if (typeof window !== 'undefined') {
    const val = localStorage.getItem('nyaya_lawyer_mode');
    if (val !== null) return val === 'true';
    return localStorage.getItem('nyaya_default_lawyer_mode') === 'true';
  }
  return false;
};

const getInitialDefaultLawyerMode = (): boolean => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem('nyaya_default_lawyer_mode') === 'true';
  }
  return false;
};

export const useStore = create<QueryState>((set) => ({
  query: '',
  setQuery: (query) => set({ query }),
  isLawyerMode: getInitialLawyerMode(),
  setIsLawyerMode: (isLawyerMode) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('nyaya_lawyer_mode', String(isLawyerMode));
    }
    set({ isLawyerMode });
  },
  defaultLawyerMode: getInitialDefaultLawyerMode(),
  setDefaultLawyerMode: (defaultLawyerMode) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('nyaya_default_lawyer_mode', String(defaultLawyerMode));
    }
    set({ defaultLawyerMode });
  },
  analysisResult: null,
  setAnalysisResult: (analysisResult) => set({ analysisResult }),
  isLoading: false,
  setIsLoading: (isLoading) => set({ isLoading }),
}));

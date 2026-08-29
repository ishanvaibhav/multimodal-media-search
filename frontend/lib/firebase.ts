import { initializeApp, getApps, getApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || "dummy-api-key-for-ssr",
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || "crypto-helper-6nm9t.firebaseapp.com",
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || "crypto-helper-6nm9t",
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET || "crypto-helper-6nm9t.appspot.com",
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || "dummy",
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID || "dummy",
};

// Only initialize Firebase on the client side
const app = typeof window !== "undefined" 
  ? (!getApps().length ? initializeApp(firebaseConfig) : getApp())
  : null;

export const auth = app ? getAuth(app) : null;

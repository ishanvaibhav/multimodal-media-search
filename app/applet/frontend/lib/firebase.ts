import { initializeApp, getApps, getApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  projectId: "crypto-helper-6nm9t",
  appId: "1:1049897728664:web:db7c756f88f2b86453eeed",
  apiKey: "AIzaSyBnYp-4r61jAPhF_baF3qGCk5vNYGdxFcM",
  authDomain: "crypto-helper-6nm9t.firebaseapp.com",
  storageBucket: "crypto-helper-6nm9t.firebasestorage.app",
  messagingSenderId: "1049897728664",
};

const app = !getApps().length ? initializeApp(firebaseConfig) : getApp();
export const auth = getAuth(app);

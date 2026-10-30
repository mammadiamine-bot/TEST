import React from "react";
import { createRoot } from "react-dom/client";
import { initConfig } from "./api";
import App from "./App";
import "./styles.css";
initConfig().then(() => createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>));

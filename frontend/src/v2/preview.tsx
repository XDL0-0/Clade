// Standalone development preview; the application entry point remains unchanged.
import { createRoot } from "react-dom/client";
import EvolutionLab from "./EvolutionLab";
const container = document.getElementById("lab-preview");
if (container) createRoot(container).render(<EvolutionLab />);

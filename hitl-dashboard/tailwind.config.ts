import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        highlight: "hsl(var(--highlight))",
        // Sacramento branded palette for direct use
        "sac-navy": "#153554",
        "sac-blue": "#7bb0da",
        "sac-gold": "#C5972C",
        "sac-dark": "#343b4b",
        "sac-light": "#e1ecf7",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        "sac": "0 1px 3px 0 rgba(21, 53, 84, 0.1), 0 1px 2px -1px rgba(21, 53, 84, 0.1)",
        "sac-md": "0 4px 6px -1px rgba(21, 53, 84, 0.1), 0 2px 4px -2px rgba(21, 53, 84, 0.1)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;

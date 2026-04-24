 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/APP_PY_TEMPLATE.py b/APP_PY_TEMPLATE.py
new file mode 100644
index 0000000000000000000000000000000000000000..73c3b8d0ff3ed0ece041561017600352f2cbd627
--- /dev/null
+++ b/APP_PY_TEMPLATE.py
@@ -0,0 +1,9 @@
+"""Thin Streamlit entrypoint.
+
+If deployment ever corrupts app.py, replace it with this exact file.
+"""
+
+from dashboard import main
+
+if __name__ == "__main__":
+    main()
 
EOF
)

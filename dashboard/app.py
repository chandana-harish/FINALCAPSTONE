import os
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime

# Import database client and settings
# Since backend is in python path, we can import from it.
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.config import settings
from backend.database import db_client

# Set page config
st.set_page_config(
    page_title="AI CI/CD Failure Analyzer",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load CSS Styles
def load_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

try:
    css_path = os.path.join(os.path.dirname(__file__), 'styles.css')
    load_css(css_path)
except Exception:
    pass # Fallback to default streamlit styling if css missing

# Check DB connection
db_available = False
db_error = None
try:
    db_client.initialize()
    db_available = True
except Exception as e:
    db_error = str(e)

# App Header
st.markdown('<div class="gradient-text">AI CI/CD Failure Analyzer</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle-text">Diagnostic platform for real-time pipeline failures and automated AI root-cause analysis</div>', unsafe_allow_html=True)

if not db_available:
    st.error("⚠️ Database Connection Offline")
    st.markdown(f"""
    <div class="glass-card" style="border-left: 4px solid #f87171;">
        <h3>Could not connect to Azure Cosmos DB</h3>
        <p>Please make sure you have created your resources in the Azure Portal and configured your <code>.env</code> file with the correct credentials.</p>
        <p><strong>Error details:</strong> <code>{db_error}</code></p>
        <hr/>
        <h4>Required Configuration variables:</h4>
        <ul>
            <li><code>COSMOS_URI</code> - Endpoint URI of Cosmos DB account</li>
            <li><code>COSMOS_KEY</code> - Cosmos DB Primary Key</li>
            <li><code>COSMOS_DATABASE</code> - Database name (Default: <code>FailureAnalyzerDB</code>)</li>
            <li><code>COSMOS_CONTAINER</code> - Container name (Default: <code>analysis_results</code>)</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# --- Load Data from Cosmos DB ---
@st.cache_data(ttl=5) # Cache data for 5 seconds to prevent spamming queries
def fetch_data():
    analyses = db_client.get_analyses(limit=100)
    summary = db_client.get_analytics_summary()
    return analyses, summary

analyses, summary = fetch_data()

# Sidebar Info and Controls
st.sidebar.markdown("### ⚙️ Platform Controls")
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### 📊 Active Pipeline Scope")
st.sidebar.info(
    f"**Organization:** `{settings.ado_org_name}`\n\n"
    f"**Target DB:** `{settings.cosmos_database}`\n\n"
    f"**Model Engine:** `{settings.openai_model_name}`"
)

st.sidebar.markdown("---")
st.sidebar.markdown("### ℹ️ Webhook Endpoint")
st.sidebar.code(
    "POST /webhook/cicd-failure",
    language="text"
)
st.sidebar.caption("Configure this endpoint URL in your Azure DevOps Service Hooks to automate analysis in real-time.")

# --- Analytics Row ---
if summary["total_failures"] > 0:
    # Render premium HTML cards for metrics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown(f"""
        <div class="metric-card failures">
            <div class="metric-title">Total Failures Analyzed</div>
            <div class="metric-value">{summary["total_failures"]}</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col2:
        severity_class = "severity-low"
        if summary["avg_severity"] >= 7.0:
            severity_class = "severity-high"
        elif summary["avg_severity"] >= 4.0:
            severity_class = "severity-medium"
            
        st.markdown(f"""
        <div class="metric-card severity">
            <div class="metric-title">Average Severity</div>
            <div class="metric-value <span class='{severity_class}'>{summary["avg_severity"]}</span>/10</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col3:
        # Determine dominant category
        class_counts = summary["classification_counts"]
        top_cat = max(class_counts, key=class_counts.get) if class_counts else "None"
        top_cat_count = class_counts.get(top_cat, 0)
        
        st.markdown(f"""
        <div class="metric-card confidence">
            <div class="metric-title">Top Failure Category</div>
            <div class="metric-value" style="font-size: 1.5rem; text-transform: uppercase;">{top_cat} ({top_cat_count})</div>
        </div>
        """, unsafe_allow_html=True)

    # Convert records to DataFrame for analysis
    df = pd.DataFrame(analyses)
    
    # Format dates
    df['created_datetime'] = pd.to_datetime(df['created_at'])
    df['date_formatted'] = df['created_datetime'].dt.strftime('%Y-%m-%d %H:%M')

    # --- Sidebar Filtering ---
    st.sidebar.markdown("### 🔍 Filters")
    
    projects = ["All"] + list(df['project_name'].unique())
    selected_project = st.sidebar.selectbox("Filter by Project", projects)
    
    classifications = ["All"] + list(df['failure_classification'].unique())
    selected_class = st.sidebar.selectbox("Filter by Failure Type", classifications)
    
    # Filter dataset
    filtered_df = df.copy()
    if selected_project != "All":
        filtered_df = filtered_df[filtered_df['project_name'] == selected_project]
    if selected_class != "All":
        filtered_df = filtered_df[filtered_df['failure_classification'] == selected_class]

    # --- Main Workspace splits into List & Details ---
    left_col, right_col = st.columns([2, 3])

    with left_col:
        st.markdown('<div class="glass-card"><h4>❌ Failed Pipeline Runs</h4>', unsafe_allow_html=True)
        
        if filtered_df.empty:
            st.warning("No failures matched the selected filters.")
            selected_run_id = None
        else:
            # Create a selection list
            run_options = []
            for _, row in filtered_df.iterrows():
                run_label = f"{row['pipeline_name']} #{row['run_number']} ({row['failure_classification'].upper()})"
                run_options.append((row['id'], run_label, row['project_name']))
                
            selected_index = st.selectbox(
                "Select a failed run to view analysis details:",
                range(len(run_options)),
                format_func=lambda x: run_options[x][1]
            )
            
            selected_run_id = run_options[selected_index][0]
            selected_run_project = run_options[selected_index][2]
            
            # Show a simple preview table
            preview_df = filtered_df[['date_formatted', 'pipeline_name', 'run_number', 'failure_classification', 'severity_score']].copy()
            preview_df.columns = ['Time', 'Pipeline', 'Run #', 'Type', 'Severity']
            st.dataframe(
                preview_df, 
                hide_index=True,
                use_container_width=True
            )
            
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Add a small distribution chart
        if not filtered_df.empty:
            st.markdown('<div class="glass-card"><h4>📈 Type Breakdown</h4>', unsafe_allow_html=True)
            fig = px.pie(
                filtered_df, 
                names='failure_classification', 
                color_discrete_sequence=px.colors.qualitative.Pastel,
                hole=0.4
            )
            fig.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font_color='#e2e8f0',
                margin=dict(t=10, b=10, l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                height=240
            )
            st.plotly_chart(fig, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

    with right_col:
        if selected_run_id:
            # Fetch full document
            run_data = db_client.get_analysis_by_id(selected_run_id, selected_run_project)
            
            if run_data:
                # Severity text / styling
                sev = run_data.get("severity_score", 5)
                sev_text = f"{sev}/10"
                if sev >= 7:
                    sev_badge = f"<span class='severity-high'>{sev_text} (Critical)</span>"
                elif sev >= 4:
                    sev_badge = f"<span class='severity-medium'>{sev_text} (Medium)</span>"
                else:
                    sev_badge = f"<span class='severity-low'>{sev_text} (Low)</span>"
                    
                class_badge_style = f"badge-{run_data.get('failure_classification', 'other')}"

                st.markdown(f"""
                <div class="glass-card">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                        <span class="badge {class_badge_style}" style="font-size: 0.9rem; padding: 6px 14px;">
                            {run_data.get('failure_classification', 'OTHER').upper()}
                        </span>
                        <div>Severity: {sev_badge}</div>
                    </div>
                    
                    <h3 style="margin-top: 0;">{run_data.get('pipeline_name')} #{run_data.get('run_number')}</h3>
                    <p style="color: #64748b; font-size: 0.9rem;">
                        Project: <b>{run_data.get('project_name')}</b> | 
                        Run ID: <code>{run_data.get('run_id')}</code> | 
                        Analyzed at: {run_data.get('created_at', '')}
                    </p>
                    
                    <hr style="border-color: rgba(255,255,255,0.05); margin: 20px 0;"/>
                    
                    <h4>🤖 AI Root Cause Diagnosis</h4>
                    <p style="font-size: 1.05rem; line-height: 1.6; color: #f1f5f9;">
                        {run_data.get('root_cause')}
                    </p>
                    
                    <div style="background: rgba(16, 185, 129, 0.08); border-left: 4px solid #10b981; border-radius: 8px; padding: 15px; margin: 20px 0;">
                        <h4 style="color: #34d399; margin-top: 0; margin-bottom: 8px;">💡 Recommended Fix Suggestion</h4>
                        <div style="font-family: inherit; font-size: 0.95rem; line-height: 1.6; color: #e2e8f0; white-space: pre-line;">
                            {run_data.get('fix_suggestion')}
                        </div>
                    </div>
                    
                    <p style="color: #64748b; font-size: 0.85rem;">
                        Confidence Score: <b>{round(run_data.get('confidence_score', 0.0) * 100)}%</b>
                    </p>
                </div>
                """, unsafe_allow_html=True)
                
                # Show raw failed log snippet in an expander
                with st.expander("📝 View Raw Failed Task Log Snippet"):
                    st.markdown(f"**Failed Step:** `{run_data.get('failed_task_name')}`")
                    st.markdown(f"""
                    <pre class="code-block">{run_data.get('log_snippet', 'No log snippet stored.')}</pre>
                    """, unsafe_allow_html=True)
            else:
                st.error("Failed to load run details. It might have been deleted.")
        else:
            st.info("Please select a failed run from the left panel to display the AI diagnostic analysis report.")

else:
    # Empty State (No records yet)
    st.info("👋 Welcome to the Failure Analyzer Platform!")
    st.markdown("""
    <div class="glass-card" style="text-align: center; padding: 40px 20px;">
        <h2 style="color: #a5b4fc;">No failures analyzed yet</h2>
        <p style="max-width: 600px; margin: 15px auto; line-height: 1.6; color: #94a3b8;">
            The platform is online and connected to Cosmos DB, but it has not received any webhook events. 
            Once an Azure DevOps pipeline fails and sends an event, the diagnostic report will appear here.
        </p>
        <div style="margin-top: 25px;">
            <p style="font-size: 0.9rem; color: #64748b;">Waiting for events on endpoint:</p>
            <code style="font-size: 1.1rem; padding: 8px 16px; background: #05070c; border-radius: 6px;">POST /webhook/cicd-failure</code>
        </div>
    </div>
    """, unsafe_allow_html=True)

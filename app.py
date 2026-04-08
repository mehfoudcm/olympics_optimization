import streamlit as st
from openai import OpenAI
import pandas as pd
from datetime import datetime, timedelta
# from st_supabase_connection import SupabaseConnection
from supabase import create_client, Client
from pulp import LpProblem, LpBinary, LpVariable, lpSum, LpMaximize

# 1. Initialize the Supabase Client
@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# 2. Function to Query Data
# We use st.cache_data to prevent hitting the DB on every user chat toggle
@st.cache_data(ttl=600) # Cache for 10 minutes
def run_query():
    # .select("*") fetches all columns; .execute() returns the response object
    return supabase.table("prices").select("*").execute()

@st.cache_data(ttl=600) # Cache for 10 minutes
def run_query_sessions():
    # .select("*") fetches all columns; .execute() returns the response object
    return supabase.table("sessions").select("*").execute()

response_sessions = run_query_sessions()
sessions_data = response_sessions.data # bringing in the sessions data for olympic events

df_sessions = pd.DataFrame(sessions_data)

st.dataframe(df_sessions)


# Assuming 'df' is the dataframe you fetched from Supabase
def flatten_prices(df):
    # Columns that stay as they are
    id_vars = [
        'Sport', 'Venue', 'Zone', 'Session Code', 'Date', 
        'Games Day', 'Session Type', 'Session Description', 
        'Start Time', 'End Time'
    ]
    
    # The price columns you want to turn into rows
    value_vars = ['Category A', 'Category B', 'Category C', 'Category D', 
                  'Category E', 'Category F', 'Category G', 'Category H', 
                  'Category I', 'Category J']

    # Unpivot the table
    df_long = pd.melt(
        df, 
        id_vars=id_vars, 
        value_vars=value_vars,
        var_name='Price Category', 
        value_name='Price'
    )
    
    # Clean up: Remove rows where Price might be null (if a category isn't offered)
    df_long = df_long.dropna(subset=['Price'])

    is_not_empty = df_long['Price'].astype(str).str.strip() != '-'
    is_not_ticketed = df_long['Session Description'].str.contains('Not Ticketed', case=False, na=False)
    
    df_filtered = df_long[is_not_empty | is_not_ticketed].copy()
    
    df_filtered['id'] = (
        df_filtered['Session Code'] + "_" + 
        df_filtered['Price Category'].str.replace('Category ', '')
    )
    
    return df_filtered

df_new = flatten_prices(pd.DataFrame(df_sessions))
st.dataframe(df_new)

def optimize_itinerary(df, max_tickets=24, total_budget=2000):
    # --- 1. Data Cleaning for Optimizer ---
    # Treat 'Not Ticketed' (-) as 0 price
    df['Price_Numeric'] = pd.to_numeric(df['Price'].replace('-', 0), errors='coerce').fillna(0)
    
    # Convert "HH:MM" to float hours (e.g., "14:30" -> 14.5) for overlap math
    def to_hours(t_str):
        h, m = map(int, t_str.split(':'))
        return h + m / 60.0

    df['start_h'] = df['Start Time'].apply(to_hours)
    df['end_h'] = df['End Time'].apply(to_hours)

    # --- 2. Initialize Model ---
    prob = LpProblem("Olympic_Optimization", LpMaximize)

    # Create a binary variable for every unique Row ID (SessionCode_Category)
    # x[i] = 1 means we attend that session at that price category
    choices = LpVariable.dicts("Select", df.index, cat=LpBinary)

    # OBJECTIVE: Maximize the number of events attended
    prob += lpSum([choices[i] for i in df.index])

    # --- 3. Constraints ---

    # Constraint A: Total Ticket Limit
    prob += lpSum([choices[i] for i in df.index]) <= max_tickets

    # Constraint B: Total Budget
    prob += lpSum([choices[i] * df.loc[i, 'Price_Numeric'] for i in df.index]) <= total_budget

    # Constraint C: Only ONE category per Session Code
    # (Stops you from buying Category A AND B for the same race)
    for code in df['Session Code'].unique():
        session_indices = df[df['Session Code'] == code].index
        prob += lpSum([choices[i] for i in session_indices]) <= 1

    # Constraint D: No Overlapping Times
    # We only check overlaps for events on the same day
    for day in df['Date'].unique():
        day_df = df[df['Date'] == day]
        unique_sessions = day_df['Session Code'].unique()
        
        # Compare every session against every other session on that day
        for i, code_a in enumerate(unique_sessions):
            for code_b in unique_sessions[i+1:]:
                # Get timing for these sessions
                row_a = day_df[day_df['Session Code'] == code_a].iloc[0]
                row_b = day_df[day_df['Session Code'] == code_b].iloc[0]
                
                # Check for overlap: StartA < EndB AND StartB < EndA
                if row_a['start_h'] < row_b['end_h'] and row_b['start_h'] < row_a['end_h']:
                    # Constraint: Selection of all categories of A + all of B must be <= 1
                    idx_a = day_df[day_df['Session Code'] == code_a].index
                    idx_b = day_df[day_df['Session Code'] == code_b].index
                    prob += lpSum([choices[k] for k in idx_a]) + lpSum([choices[m] for m in idx_b]) <= 1

    # --- 4. Solve ---
    prob.solve()

    # --- 5. Extract Results ---
    selected_rows = [i for i in df.index if value(choices[i]) == 1]
    return df.loc[selected_rows]

# --- Streamlit UI Integration ---
st.title("🏅 Olympic Itinerary Optimizer")

# User Controls
budget = st.sidebar.slider("Max Budget (€)", 100, 5000, 1500)
tickets = st.sidebar.slider("Max Tickets", 1, 24, 12)

if st.button("Generate Optimized Schedule"):
    # Assuming 'clean_df' is the result of your previous transformation
    itinerary = optimize_itinerary(df_new, max_tickets=tickets, total_budget=budget)
    
    st.write(f"### Found {len(itinerary)} events within your constraints:")
    st.dataframe(itinerary[['Sport', 'Session Description', 'Date', 'Start Time', 'Price Category', 'Price']])
    
    total_cost = itinerary['Price_Numeric'].sum()
    st.metric("Total Estimated Cost", f"€{total_cost:,.2f}")

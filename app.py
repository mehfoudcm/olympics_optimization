import streamlit as st
from openai import OpenAI
import pandas as pd
from datetime import datetime, timedelta
# from st_supabase_connection import SupabaseConnection
from supabase import create_client, Client
from pulp import LpProblem, LpMaximize, LpVariable, lpSum, LpInteger, LpBinary, value

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


st.write("Full Event Table")
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

    df_filtered = df_filtered[df_filtered.Zone != 'OKC']
    df_filtered = df_filtered[df_filtered.Zone != 'New York']
    df_filtered = df_filtered[df_filtered.Zone != 'St. Louis']
    df_filtered = df_filtered[df_filtered.Zone != 'Columbus']
    df_filtered = df_filtered[df_filtered.Zone != 'Nashville']
    df_filtered = df_filtered[df_filtered.Zone != 'San José']

    df_filtered = df_filtered[df_filtered['Start Time'] != 'TBD']

    
    # Treat 'Not Ticketed' (-) as 0 price
    df_filtered['Price_Num'] = df_filtered['Price'].astype(str)
    
    # Remove currency symbols, commas, and the dash
    df_filtered['Price_Num'] = df_filtered['Price_Num'].str.replace('$', '', regex=False)
    df_filtered['Price_Num'] = df_filtered['Price_Num'].str.replace(',', '', regex=False)
    df_filtered['Price_Num'] = df_filtered['Price_Num'].str.replace(' - ', '0', regex=False)
    df_filtered['Price_Num'] = df_filtered['Price_Num'].str.strip()

    # Convert to float; invalid values become NaN, then fill NaN with 0
    df_filtered['Price_Num'] = pd.to_numeric(df_filtered['Price_Num'], errors='coerce').fillna(0.0)
    df_filtered['Games Day'] = pd.to_numeric(df_filtered['Games Day'], errors='coerce').fillna(0).astype(int)
    df_filtered = df_filtered[df_filtered['Games Day'] >= 11]

    df_filtered['Date_Str'] = df_filtered['Date'].astype(str)
    df_filtered['Time_Str'] = df_filtered['Start Time'].astype(str)
    
    # 2. Combine them into one string
    # We use .strip() to remove any accidental spaces
    combined_str = df_filtered['Date_Str'].str.strip()+ ', 2028 ' + df_filtered['Time_Str'].str.strip()
    
    # 3. Convert to actual Python Datetime objects
    # errors='coerce' turns "Not Ticketed" or empty times into NaT (Not a Time)
    df_filtered['Session Start Date Time'] = pd.to_datetime(combined_str, errors='coerce')
    df_filtered = df_filtered.drop(columns=['Date_Str', 'Time_Str'])
    
    return df_filtered

def filter_conflicting_events(df, mandatory_requirements):
    if not mandatory_requirements:
        return df

    # 1. Get the full data for the mandatory events
    mandatory_ids = list(mandatory_requirements.keys())
    mandatory_df = df[df['id'].isin(mandatory_ids)]
    
    # 2. Create a list of 'blocked' windows
    # We store these as (Date, Start, End) tuples
    blocked_windows = []
    for _, row in mandatory_df.iterrows():
        blocked_windows.append({
            'date': row['Date'],
            'start': row['Start Time'],
            'end': row['End Time'],
            'id': row['id']
        })

    # 3. Define the filtering function
    def is_conflicting(row):
        # If this row IS one of the mandatory events, keep it!
        if row['id'] in mandatory_ids:
            return False
            
        for window in blocked_windows:
            # Check if it's the same day
            if row['Date'] == window['date']:
                # Overlap logic: StartA < EndB AND StartB < EndA
                if row['start'] < window['end'] and window['start'] < row['end']:
                    return True # Conflict found
        return False

    # 4. Apply the filter
    # We keep rows that are NOT conflicting
    filtered_df = df[~df.apply(is_conflicting, axis=1)].copy()
    
    return filtered_df




df_new = flatten_prices(pd.DataFrame(df_sessions))

tab1, tab2 = st.tabs(["⚙️ Settings & Mandatory Events", "📊 Optimized Results"])

with tab1:
    st.header("Filter Options")

        
    # User Controls
    budget = st.slider("Max Budget ($)", 1000, 20000, 2000)
    tickets = st.slider("Max Tickets", 1, 24, 12)
    
    # 1. Get unique zones (sorted)
    # Ensure we drop any null values so the selector doesn't crash
    all_zones = sorted(df_new['Zone'].dropna().unique().tolist())
    
    # 2. Multiselect for Zones
    selected_zones = st.multiselect(
        "Select Target Zones",
        options=all_zones,
        default=all_zones  # Defaults to all zones selected
    )
    
    # 3. Apply the filter to the dataframe
    # This happens BEFORE the optimizer sees the data
    df_new_zone = df_new[df_new['Zone'].isin(selected_zones)].copy()
    
    # 4. Display a warning if no zones are selected
    if not selected_zones:
        st.warning("Please select at least one Zone in the sidebar.")
        st.stop()

    ## bringing in mandatory events
    st.header("Mandatory Events")
    
    # Helper for the label
    df_new_zone['label'] = df_new_zone['Session Description'] + " (" + df_new_zone['id'] + ")"
    
    # 1. Select the events
    selected_labels = st.multiselect(
        "Select events you MUST attend:",
        options=df_new_zone['label'].unique()
    )
    
    # 2. Decide the quantity for each selected event
    mandatory_requirements = {}
    for label in selected_labels:
        # Find the ID for this label
        event_id = df_new_zone[df_new_zone['label'] == label]['id'].iloc[0]
        
        qty = st.number_input(
            f"Tickets for: {label}", 
            min_value=1, 
            max_value=4, 
            value=1,
            key=f"qty_{event_id}"
        )
        mandatory_requirements[event_id] = qty



     #--- Pre-Optimization Validation ---

    # 1. Calculate totals for mandatory selections
    mandatory_qty_total = sum(mandatory_requirements.values())
    mandatory_cost_total = sum(
        df_new_zone[df_new_zone['id'] == eid]['Price_Num'].iloc[0] * qty 
        for eid, qty in mandatory_requirements.items()
    )
    
    # 2. Check for Overlaps in Mandatory Selections
    # We filter the DF to just the mandatory events to check their timing
    mandatory_df = df_new_zone[df_new_zone['id'].isin(mandatory_requirements.keys())]
    has_time_conflict = False
    conflict_details = ""
    
    # Simple overlap check: compare each mandatory event against others
    m_sessions = mandatory_df.to_dict('records')
    print(m_sessions)
    for i, event_a in enumerate(m_sessions):
        for event_b in m_sessions[i+1:]:
            # If same day and times overlap
            if event_a['Date'] == event_b['Date']:
                if event_a['Start Time'] < event_b['End Time'] and event_b['Start Time'] < event_a['End Time']:
                    has_time_conflict = True
                    conflict_details = f"'{event_a['Session Description']}' and '{event_b['Session Description']}' overlap."

    
    st.markdown("---")
    st.subheader("Current Requirements")
    col1, col2 = st.columns(2)
    
    # Color the text red if it exceeds the limit
    ticket_color = "red" if mandatory_qty_total > tickets else "green"
    budget_color = "red" if mandatory_cost_total > budget else "green"
    
    col1.markdown(f"Tickets: :{ticket_color}[{mandatory_qty_total} / {tickets}]")
    col2.markdown(f"Cost: :{budget_color}[${mandatory_cost_total:,.0f}]")


with tab2:
    st.write("Time Constrained Event Table")

    clean_filtered_df = filter_conflicting_events(df_new_zone, mandatory_requirements)

    st.dataframe(clean_filtered_df)

    #--- Pre-Optimization Validation ---
    # --- Streamlit UI Integration ---
    st.title("🏅 Olympic Itinerary Optimizer")

    
    # --- 3. The UI Error Handling ---
    if st.button("Optimize My Schedule"):
        if mandatory_qty_total > tickets:
            st.error(f"🚫 **Too many tickets:** You've selected {mandatory_qty_total} mandatory tickets, but your limit is {tickets}.")
        
        elif mandatory_cost_total > budget:
            st.error(f"🚫 **Budget Exceeded:** Mandatory events cost ${mandatory_cost_total:,.2f}, exceeding your ${budget:,.2f} budget.")
        
        elif has_time_conflict:
            st.error(f"🚫 **Schedule Conflict:** {conflict_details}")
            
        else:
            # All checks passed! Proceed to optimization
            with st.spinner("Calculating optimal gaps..."):
                # results = optimize_itinerary(df, max_tix, total_budget, mandatory_requirements)
                # ... display results ...
    
                itinerary = optimize_itinerary(df_new_zone, max_tickets=tickets, total_budget=budget)
            
                itinerary['Total Cost'] = itinerary['Selected_Qty']*itinerary['Price_Num']
            
                
                st.write(f"### Found {len(itinerary)} events within your constraints:")
                st.dataframe(itinerary[['id', 'Sport', 'Session Description', 'Selected_Qty', 'Date', 'Games Day', 'Start Time', 'End Time', 'Session Start Date Time', 'Price Category', 'Price', 'Total Cost']])
            
                st.write(f"### Planned to buy {itinerary['Selected_Qty'].sum()} total tickets")
            
                total_cost = (itinerary['Selected_Qty']*itinerary['Price_Num']).sum()
                st.metric("Total Estimated Cost", f"${total_cost:,.2f}")
            
                if not itinerary.empty:
                    first_day = itinerary['Games Day'].min()
                    last_day = itinerary['Games Day'].max()
                    window_length = last_day - first_day + 1
                    
                    st.info(f"📅 **Travel Window:** Day {first_day} to Day {last_day} ({window_length} days total)")
                    
                    # Optional: Filter the dataframe to show the schedule chronologically
                    st.dataframe(itinerary.sort_values(['Games Day', 'start_h']))
        





# st.sidebar.header("Mandatory Events")

# # Create a helper column for a readable label in the dropdown
# df_new_zone['label'] = (
#     df_new_zone['id'] + ":" + df_new_zone['Session Description'] + 
#     " (" + df_new_zone['Price Category'] + ": $" + 
#     df_new_zone['Price'].astype(str) + ")"
# )

# # Multiselect for mandatory events
# must_attend_labels = st.sidebar.multiselect(
#     "Select events you MUST attend:",
#     options=df_new_zone['label'].unique()
# )

# # Map those labels back to the unique 'id'
# must_attend_ids = df_new_zone[df_new_zone['label'].isin(must_attend_labels)]['id'].tolist()


# def optimize_itinerary(df, max_tickets=24, total_budget=2000):
#     # --- 1. Data Cleaning for Optimizer ---

    
#     # Convert "HH:MM" to float hours (e.g., "14:30" -> 14.5) for overlap math
#     def to_hours(t):
#         # If it's already a number (float/int), just return it
#         if isinstance(t, (int, float)):
#             return float(t)
        
#         # If it's a string, split it
#         if isinstance(t, str) and ':' in t:
#             try:
#                 h, m = map(int, t.split(':'))
#                 if h == 0:
#                     return 24
#                 return h + m / 60.0
#             except ValueError:
#                 return 0.0 # Return 0 if the string is malformed (e.g., "TBD")
                
#         return 0.0

#     df['start_h'] = df['Start Time'].apply(to_hours)
#     df['end_h'] = df['End Time'].apply(to_hours)

#     # --- 2. Initialize Model ---
#     prob = LpProblem("Olympic_Optimization", LpMaximize)

#     # Create a binary variable for every unique Row ID (SessionCode_Category)
#     # x[i] = 1 means we attend that session at that price category
#     choices = LpVariable.dicts("Select", df.index, cat=LpBinary)

#     # OBJECTIVE: Maximize the number of events attended
#     prob += lpSum([choices[i] for i in df.index])

#     # --- 3. Constraints ---

#     # MUST ATTEND EVENTS
#     for i in df.index:
#         if df.loc[i, 'id'] in must_attend_ids:
#             # This forces the optimizer to pick this specific row
#             prob += choices[i] == 1
    
#     # Constraint A: Total Ticket Limit
#     prob += lpSum([choices[i] for i in df.index]) <= max_tickets

#     # Constraint B: Total Budget
#     prob += lpSum([choices[i] * df.loc[i, 'Price_Num'] for i in df.index]) <= total_budget

#     # Constraint for 5 day window
#     # all_days = sorted(df['Games Day'].unique())
#     # for d1 in all_days:
#     #     for d2 in all_days:
#     #         if d2 - d1 > 4:
#     #             # If d2 is more than 4 days after d1, you cannot pick BOTH.
#     #             # Logic: choices in d1 + choices in d2 must be restricted
#     #             idx_day1 = df[df['Games Day'] == d1].index
#     #             idx_day2 = df[df['Games Day'] == d2].index
                
#     #             for i in idx_day1:
#     #                 for j in idx_day2:
#     #                     # Constraint: You cannot select both event i and event j
#     #                     prob += choices[i] + choices[j] <= 1
    
#     # Constraint C: Only ONE category per Session Code
#     # (Stops you from buying Category A AND B for the same race)
#     for code in df['Session Code'].unique():
#         session_indices = df[df['Session Code'] == code].index
#         prob += lpSum([choices[i] for i in session_indices]) <= 1

#     # Constraint D: No Overlapping Times
#     # We only check overlaps for events on the same day
#     for day in df['Date'].unique():
#         day_df = df[df['Date'] == day]
#         unique_sessions = day_df['Session Code'].unique()
        
#         # Compare every session against every other session on that day
#         for i, code_a in enumerate(unique_sessions):
#             for code_b in unique_sessions[i+1:]:
#                 # Get timing for these sessions
#                 row_a = day_df[day_df['Session Code'] == code_a].iloc[0]
#                 row_b = day_df[day_df['Session Code'] == code_b].iloc[0]
                
#                 # Check for overlap: StartA < EndB AND StartB < EndA
#                 if row_a['start_h'] < row_b['end_h'] and row_b['start_h'] < row_a['end_h']:
#                     # Constraint: Selection of all categories of A + all of B must be <= 1
#                     idx_a = day_df[day_df['Session Code'] == code_a].index
#                     idx_b = day_df[day_df['Session Code'] == code_b].index
#                     prob += lpSum([choices[k] for k in idx_a]) + lpSum([choices[m] for m in idx_b]) <= 1

#     # --- 4. Solve ---
#     prob.solve()

#     # --- 5. Extract Results ---
#     selected_rows = [i for i in df.index if value(choices[i]) == 1]
#     return df.loc[selected_rows]


def optimize_itinerary(df, max_tickets=24, total_budget=20000, must_attend_ids=[]):
    # --- 1. Data Cleaning ---
    def to_hours(t):
        if isinstance(t, (int, float)): return float(t)
        if isinstance(t, str) and ':' in t:
            try:
                parts = t.split(':')
                h, m = int(parts[0]), int(parts[1])
                return 24 if h == 0 else h + m / 60.0
            except ValueError: return 0.0
        return 0.0

    df['start_h'] = df['Start Time'].apply(to_hours)
    df['end_h'] = df['End Time'].apply(to_hours)

    # --- 2. Initialize Model ---
    prob = LpProblem("Olympic_MultiTicket_Optimization", LpMaximize)

    # Main Variable: How many tickets to buy for this specific row (0 to 4)
    # Use 'Integer' instead of 'Binary'
    quantities = LpVariable.dicts("Qty", df.index, lowBound=0, upBound=4, cat=LpInteger)
    
    # Helper Variable: Is this specific session/category selected at all? (0 or 1)
    is_selected = LpVariable.dicts("IsSelected", df.index, cat=LpBinary)

    # OBJECTIVE: Maximize the total number of tickets purchased
    prob += lpSum([quantities[i] for i in df.index])

    # --- 3. Constraints ---

    # Linking Constraint: If quantities[i] > 0, then is_selected[i] must be 1
    # 1. Linking Quantities and Selection
    for i in df.index:
        prob += quantities[i] <= 4 * is_selected[i]
        prob += quantities[i] >= 1 * is_selected[i]

    # 2. MANDATORY REQUIREMENTS (Dynamic Quantities)
    for i in df.index:
        event_id = df.loc[i, 'id']
        if event_id in mandatory_requirements:
            required_qty = mandatory_requirements[event_id]
            # Force the exact quantity requested in the sidebar
            prob += quantities[i] == required_qty
            prob += is_selected[i] == 1
    
    # 3. Budget (Price * Qty)
    prob += lpSum([quantities[i] * df.loc[i, 'Price_Num'] for i in df.index]) <= total_budget

    # 4. Total Ticket Limit
    prob += lpSum([quantities[i] for i in df.index]) <= max_tickets

    # 5. One Category per Session
    for code in df['Session Code'].unique():
        s_idx = df[df['Session Code'] == code].index
        prob += lpSum([is_selected[i] for i in s_idx]) <= 1
        
    # Constraint D: No Overlapping Times
    # If sessions overlap, you can't be in two places at once. 
    # Therefore, the sum of "is_selected" for overlapping sessions must be <= 1
    for day in df['Date'].unique():
        day_df = df[df['Date'] == day]
        unique_sessions = day_df['Session Code'].unique()
        
        for i, code_a in enumerate(unique_sessions):
            for code_b in unique_sessions[i+1:]:
                row_a = day_df[day_df['Session Code'] == code_a].iloc[0]
                row_b = day_df[day_df['Session Code'] == code_b].iloc[0]
                
                if row_a['start_h'] < row_b['end_h'] and row_b['start_h'] < row_a['end_h']:
                    idx_a = day_df[day_df['Session Code'] == code_a].index
                    idx_b = day_df[day_df['Session Code'] == code_b].index
                    # You can pick categories from A OR categories from B, but not both
                    prob += lpSum([is_selected[k] for k in idx_a]) + lpSum([is_selected[m] for m in idx_b]) <= 1

    # --- 4. Solve ---
    prob.solve()

    # --- 5. Extract Results ---
    # We filter rows where the quantity is greater than 0
    selected_indices = [i for i in df.index if value(quantities[i]) >= 1]
    
    result_df = df.loc[selected_indices].copy()
    # Add the selected quantity back to the dataframe for display
    result_df['Selected_Qty'] = [int(value(quantities[i])) for i in selected_indices]
    
    return result_df


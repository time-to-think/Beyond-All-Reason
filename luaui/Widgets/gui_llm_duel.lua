local widget = widget ---@type Widget

function widget:GetInfo()
	return {
		name = "LLM Duel Bridge",
		desc = "Nonblocking loopback transport for the capstone duel",
		author = "Capstone team / Codex",
		license = "GNU GPL v2 or later",
		layer = 0,
		enabled = true,
	}
end
local opts = Spring.GetModOptions()
if tostring(opts.llm_duel or "") ~= "1" then
	return false
end
local json = Json or require("common/luaUtilities/json")
local conn, pending, buffer, outgoing = nil, nil, "", ""
local finalPayload
local quitDelay
local elapsed, retry = 0, 0
local token = tostring(opts.llm_duel_token or "")
local label = "Connecting to model bridge"
local function snapshot(payload)
	pending = payload
	local ok, decoded = pcall(json.decode, payload)
	if ok and decoded.kind == "finished" then
		finalPayload = payload
	end
end
function widget:Initialize()
	if Spring.GetMyPlayerID() ~= 0 or not socket then
		Spring.Echo("BAR_DUEL: LuaSocket unavailable or not host")
		widgetHandler:RemoveWidget(self)
		return
	end
	widgetHandler:RegisterGlobal("BARDuelSnapshot", snapshot)
end
local function disconnect()
	if conn then
		conn:close()
	end
	conn = nil
	buffer = ""
	outgoing = ""
	retry = 3
end
function widget:Update(dt)
	if quitDelay then
		quitDelay = quitDelay - dt
		if quitDelay <= 0 then
			Spring.SendCommands("quitforce")
			quitDelay = nil
		end
	end
	retry = retry - dt
	if not conn then
		if retry > 0 then
			return
		end
		conn = socket.tcp()
		conn:settimeout(0)
		local ok, err = conn:connect("127.0.0.1", tonumber(opts.llm_duel_port) or 18765)
		if not ok and err ~= "timeout" then
			disconnect()
			return
		end
	end
	elapsed = elapsed + dt
	if outgoing == "" and elapsed >= 0.5 then
		elapsed = 0
		local value = { kind = "poll" }
		if finalPayload or pending then
			local ok, decoded = pcall(json.decode, finalPayload or pending)
			if ok then
				value = decoded
			end
			pending = nil
		end
		value.token = token
		outgoing = json.encode(value) .. "\n"
	end
	if #outgoing > 0 then
		local sent, err, partial = conn:send(outgoing)
		outgoing = outgoing:sub((sent or partial or 0) + 1)
		if err and err ~= "timeout" then
			disconnect()
			return
		end
	end
	local line, err, partial = conn:receive("*l")
	if line then
		local ok, data = pcall(json.decode, buffer .. line)
		buffer = ""
		if ok then
			if data.finished then
				finalPayload = nil
				label = "Match finished - results saved"
				if tostring(opts.llm_duel_test_exit or "") == "1" and not quitDelay then
					quitDelay = 2
				end
			end
			for _, c in ipairs(data.commands or {}) do
				c.token = token
				Spring.SendLuaRulesMsg("BARDUEL: " .. json.encode(c))
				label = (c.team == 0 and "LOCAL" or "CLOUD") .. ": " .. c.action .. " / " .. c.unit
				Spring.Echo("BAR_DUEL_RECEIVED team=" .. c.team .. " action=" .. c.action)
			end
		end
	else
		buffer = buffer .. (partial or "")
		if #buffer > 131072 or (err and err ~= "timeout") then
			disconnect()
		end
	end
end
function widget:DrawScreen()
	local _, h = Spring.GetViewGeometry()
	gl.Text(
		"LLM DUEL | Local: "
			.. tostring(opts.llm_local_model or "local")
			.. " | Cloud: "
			.. tostring(opts.llm_cloud_model or "cloud"),
		24,
		h - 70,
		16,
		"o"
	)
	gl.Text(label, 24, h - 94, 14, "o")
end
function widget:Shutdown()
	widgetHandler:DeregisterGlobal("BARDuelSnapshot")
	disconnect()
end
